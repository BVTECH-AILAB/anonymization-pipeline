"""NER detector: a local model that proposes values with their category.

Two engines behind one interface (``Engine``):
  - ``token_classification``: a Hugging Face model with fixed labels (NOME, COGNOME, MALATTIA...),
    mapped by ``label_map`` of ``resources/detectors/ner.yaml``;
  - ``gliner``: a GLiNER model asked for the labels of ``gliner_labels`` (zero-shot).
The text is split into overlapping word windows; values go through the filters of ``ner.yaml``
(labels and common words mistaken for names) and adjacent NOME and COGNOME become a PERSONA.

The model is loaded once by the application lifespan (``load_engine``) from the Hugging Face
cache, without network, and shared by every document; it is downloaded by
``python -m anonymization_pipeline.model_store`` (``just models``). Without the ``ner`` extra or
without the model, ``load_engine`` raises ``DetectorUnavailable``.
"""

import functools
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from anonymization_pipeline.detectors.base import DetectorUnavailable, Proposal
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.resources import read_yaml
from anonymization_pipeline.pipeline_config.schema import Ner

try:
    import torch
    from gliner import GLiNER
    from huggingface_hub import list_repo_files, snapshot_download
    from transformers import pipeline as hf_pipeline

    NER_INSTALLED = True
except ImportError:  # image without the 'ner' extra
    NER_INSTALLED = False

logger = logging.getLogger(__name__)

FILE = "detectors/ner.yaml"
_KEYS = (
    "label_map",
    "gliner_labels",
    "min_length",
    "require_capital",
    "false_positives",
)
# weight formats that are not needed (only safetensors are downloaded, when the repository has them)
# configuration and tokenizer of a GLiNER base encoder (its weights are in the GLiNER file)
_TOKENIZER_FILES = ["*.json", "*.model", "*.txt", "spm*"]
_EXCLUDED_FORMATS = [
    "*.onnx",
    "onnx/*",
    "*.h5",
    "*.msgpack",
    "*.ot",
    "flax_model*",
    "tf_model*",
]

# (start, end, label, score) with offsets in the text given to the engine
Prediction = tuple[int, int, str, float]


@dataclass(frozen=True)
class Vocabulary:
    """Content of ``ner.yaml``, validated.

    Attributes:
        label_map: Token classification label (uppercase) -> category.
        gliner_labels: GLiNER label -> category.
        min_length: Shorter values are discarded.
        require_capital: Categories whose values need at least one capitalized word.
        false_positives: Lowercase words often mistaken for proper names (labels, headings).
    """

    label_map: dict[str, str]
    gliner_labels: dict[str, str]
    min_length: int
    require_capital: frozenset[str]
    false_positives: frozenset[str]

    def category(self, engine: str, label: str) -> str | None:
        if engine == "gliner":
            return self.gliner_labels.get(label)
        return self.label_map.get(label.upper())


@functools.cache
def vocabulary() -> Vocabulary:
    """Vocabulary of ``ner.yaml`` (read at first use, then cached).

    Raises:
        ValueError: On a missing or unknown key, a wrong type or a nonexistent category.
    """
    data = read_yaml(FILE)
    if missing := [k for k in _KEYS if k not in data]:
        raise ValueError(f"{FILE}: missing keys {', '.join(missing)}")
    if unknown := sorted(set(data) - set(_KEYS)):
        raise ValueError(f"{FILE}: unknown keys {', '.join(map(str, unknown))}")
    for key in ("label_map", "gliner_labels"):
        if not isinstance(data[key], dict):
            raise ValueError(f"{FILE}, {key}: expected a mapping label: category")
    for key in ("require_capital", "false_positives"):
        if not isinstance(data[key], list):
            raise ValueError(f"{FILE}, {key}: expected a list")
    minimum = data["min_length"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError(f"{FILE}, min_length: expected an integer >= 1")
    label_map = {str(k).upper(): str(v) for k, v in data["label_map"].items()}
    gliner_labels = {str(k): str(v) for k, v in data["gliner_labels"].items()}
    require_capital = frozenset(str(c) for c in data["require_capital"])
    categories = set(label_map.values()) | set(gliner_labels.values()) | require_capital
    if unknown := sorted(categories - set(registry().descriptions)):
        raise ValueError(
            f"{FILE}: unknown categories {', '.join(unknown)} (see categories.yaml)"
        )
    return Vocabulary(
        label_map=label_map,
        gliner_labels=gliner_labels,
        min_length=minimum,
        require_capital=require_capital,
        false_positives=frozenset(str(p).lower() for p in data["false_positives"]),
    )


class Engine(Protocol):
    """A loaded NER model: text -> predictions (not thread-safe: the caller holds ``lock``)."""

    lock: threading.Lock

    def predict(self, text: str) -> list[Prediction]: ...


class TokenClassificationEngine:
    """Hugging Face token classification pipeline, labels aggregated per entity."""

    def __init__(self, run: Callable[[str], list[dict[str, Any]]]) -> None:
        self._run = run
        self.lock = threading.Lock()

    def predict(self, text: str) -> list[Prediction]:
        return [
            (int(i["start"]), int(i["end"]), str(i["entity_group"]), float(i["score"]))
            for i in self._run(text)
        ]


class GlinerEngine:
    """GLiNER asked for a fixed set of labels."""

    def __init__(self, model: Any, labels: list[str], threshold: float) -> None:
        self._model = model
        self._labels = labels
        self._threshold = threshold
        self.lock = threading.Lock()

    def predict(self, text: str) -> list[Prediction]:
        found = self._model.predict_entities(
            text, self._labels, threshold=self._threshold
        )
        return [
            (int(e["start"]), int(e["end"]), str(e["label"]), float(e["score"]))
            for e in found
        ]


def _is_local(model: str) -> bool:
    return Path(model).is_absolute() or model.startswith(".")


def model_dir(cfg: Ner) -> str:
    """Folder of the model in the cache (or the folder of the configuration), without network.

    Raises:
        DetectorUnavailable: The library or the model is missing.
    """
    if _is_local(cfg.model):
        if not Path(cfg.model).is_dir():
            raise DetectorUnavailable(f"NER model folder '{cfg.model}' does not exist")
        return cfg.model
    if not NER_INSTALLED:
        raise DetectorUnavailable(
            "NER libraries not installed (image without the 'ner' extra)"
        )
    try:
        folder = snapshot_download(
            cfg.model,
            revision=cfg.revision,
            local_files_only=True,
            allow_patterns=["*.json"],
        )
    except (
        Exception
    ) as exc:  # huggingface_hub raises several types for a missing cache entry
        raise DetectorUnavailable(
            f"NER model '{cfg.model}' missing in the cache (just models): {type(exc).__name__}"
        ) from exc
    if not any(f.suffix in (".safetensors", ".bin") for f in Path(folder).iterdir()):
        raise DetectorUnavailable(
            f"weights of the NER model '{cfg.model}' missing in the cache"
        )
    return folder


def _base_encoder(folder: str) -> str | None:
    """Base encoder a GLiNER model loads its configuration and tokenizer from."""
    config = Path(folder) / "gliner_config.json"
    if not config.is_file():
        return None
    name = json.loads(config.read_text(encoding="utf-8")).get("model_name")
    return str(name) if name else None


def download(cfg: Ner) -> str:
    """Download the model into the cache (only safetensors when available); return its folder.

    A GLiNER model also needs the configuration and the tokenizer of its base encoder: they are
    downloaded too, without the encoder weights (the GLiNER file holds them).

    Raises:
        DetectorUnavailable: The 'ner' extra is not installed.
    """
    try:
        folder = model_dir(cfg)
    except DetectorUnavailable:
        if not NER_INSTALLED or _is_local(cfg.model):
            raise
        files = list_repo_files(cfg.model, revision=cfg.revision)
        excluded = _EXCLUDED_FORMATS + (
            ["*.bin"] if any(f.endswith(".safetensors") for f in files) else []
        )
        logger.info("Downloading the NER model into the Hugging Face cache")
        folder = snapshot_download(
            cfg.model, revision=cfg.revision, ignore_patterns=excluded
        )
    if (base := _base_encoder(folder)) is not None:
        snapshot_download(base, allow_patterns=_TOKENIZER_FILES)
    return folder


def load_engine(cfg: Ner) -> Engine:
    """Load the configured model from the cache.

    Raises:
        DetectorUnavailable: Library or model missing, or the model cannot be loaded.
    """
    folder = model_dir(cfg)
    try:
        if cfg.threads and torch.get_num_threads() != cfg.threads:
            # with every core inference is much slower because of thread contention
            torch.set_num_threads(cfg.threads)
        if cfg.engine == "gliner":
            model = GLiNER.from_pretrained(folder, local_files_only=True)
            labels = list(vocabulary().gliner_labels)
            engine: Engine = GlinerEngine(model.to("cpu"), labels, cfg.threshold)
        else:
            run = hf_pipeline(
                "token-classification",
                model=folder,
                aggregation_strategy="simple",
                device="cpu",
            )
            engine = TokenClassificationEngine(run)
    except Exception as exc:  # any loading failure makes the detector unavailable
        raise DetectorUnavailable(
            f"NER model '{cfg.model}' cannot be loaded: {type(exc).__name__}"
        ) from exc
    logger.info("NER model loaded: engine=%s model=%s", cfg.engine, cfg.model)
    return engine


def windows(text: str, words: int, overlap: int) -> list[tuple[int, int]]:
    """Blocks of ``words`` words that overlap by ``overlap``, as offsets in the text."""
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    if not spans:
        return []
    step = max(1, words - max(0, min(overlap, words - 1)))
    out = []
    for i in range(0, len(spans), step):
        j = min(len(spans), i + words)
        out.append((spans[i][0], spans[j - 1][1]))
        if j == len(spans):
            break
    return out


class NerDetector:
    """Text detector on top of a loaded engine.

    Args:
        cfg: NER configuration of the run.
        engine: The model loaded at startup.
    """

    name = "ner"

    def __init__(self, cfg: Ner, engine: Engine) -> None:
        self.cfg = cfg
        self.engine = engine
        self.vocabulary = vocabulary()
        reg = registry()
        self.person_categories = {
            c for c in reg.descriptions if reg.family(c) == "PERSONA"
        }

    def detect(self, text: str) -> list[Proposal]:
        if not text.strip():
            return []
        best: dict[tuple[int, int, str], float] = {}
        with self.engine.lock:  # one page at a time
            for s0, e0 in windows(text, self.cfg.window_words, self.cfg.overlap_words):
                for start, end, label, score in self.engine.predict(text[s0:e0]):
                    category = self.vocabulary.category(self.cfg.engine, label)
                    if category and score >= self.cfg.threshold:
                        key = (s0 + start, s0 + end, category)
                        best[key] = max(best.get(key, 0.0), score)
        spans = self._merge_names(text, sorted(best))
        kept = (self._filter(text, s, e, c) for s, e, c in spans)
        return list(
            dict.fromkeys(Proposal(text[s:e], c) for s, e, c in kept if s is not None)
        )

    def _merge_names(
        self, text: str, spans: list[tuple[int, int, str]]
    ) -> list[tuple[int, int, str]]:
        """Adjacent NOME and COGNOME (separated only by spaces) become a PERSONA: the model labels
        them separately and, alone, they would be redacted as parts."""
        out: list[tuple[int, int, str]] = []
        for s, e, c in sorted(spans):
            if (
                out
                and c in ("NOME", "COGNOME")
                and out[-1][2] in ("NOME", "COGNOME")
                and 0 < s - out[-1][1] <= 2
                and text[out[-1][1] : s].isspace()
            ):
                out[-1] = (out[-1][0], e, "PERSONA")
            else:
                out.append((s, e, c))
        return out

    def _filter(
        self, text: str, s: int, e: int, category: str
    ) -> tuple[int | None, int, str]:
        """Discard labels and common words mistaken for names (start None if ignored)."""
        f = self.vocabulary
        value = text[s:e].strip()
        s = s + text[s:e].find(value) if value else s
        e = s + len(value)
        # inner newlines: they often join label and value
        if "\n" in value or len(value) < f.min_length:
            return None, e, category
        tokens = [t for t in re.split(r"\s+", value) if t]
        if not tokens or all(
            t.lower().strip(".:,*|") in f.false_positives for t in tokens
        ):
            return None, e, category
        # a proper name has at least one capitalized word
        if category in f.require_capital and not any(
            re.match(r"[A-ZÀ-Ý]", t) for t in tokens
        ):
            return None, e, category
        # word followed by ":" = label of a field ("Indirizzo:", "Ambulatorio :")
        if len(tokens) == 1 and re.match(r"[ \t.*]*:", text[e : e + 4]):
            return None, e, category
        if category in self.person_categories:
            if not any(re.match(r"[A-ZÀ-Ý]", t) for t in tokens) or re.search(
                r"\d", value
            ):
                return None, e, category
            # a single all-caps word: usually a title or a heading ("DIAGNOSI")
            if len(tokens) == 1 and value.isupper():
                return None, e, category
            # remove leading label words ("Paziente Mario Rossi")
            while tokens and tokens[0].lower().strip(".:,") in f.false_positives:
                tokens = tokens[1:]
            if not tokens:
                return None, e, category
            s = s + value.find(tokens[0])
            if e - s < f.min_length:
                return None, e, category
        return s, e, category
