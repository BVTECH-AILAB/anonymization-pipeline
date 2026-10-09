"""NER detector: Hugging Face token-classification model (default Italian_NER_XXL_v2).

The model has fixed labels (NOME, COGNOME, MALATTIA, MEDICINA...): a map in the configuration
brings them to the anonymizer categories; unmapped labels are ignored. The text is split into
overlapping word windows (the model reads at most 512 tokens). It runs on CPU, in a thread: the
GPU stays with the VLM.

The model is loaded from the Hugging Face cache (HF_HOME) without network; it is downloaded by
``python -m anonymization_pipeline.model_store``. Without the ``ner`` extra or without the model
the detector raises ``DetectorUnavailable`` and the pipeline skips it with a warning.
Ported from the anonimizzatore project (token_classification engine).
"""

import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from anonymization_pipeline.detectors.base import DetectorUnavailable, Proposal
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.schema import Ner

try:
    import torch
    from huggingface_hub import list_repo_files, snapshot_download
    from transformers import pipeline as hf_pipeline

    NER_INSTALLED = True
except ImportError:  # image without the 'ner' extra
    NER_INSTALLED = False

logger = logging.getLogger(__name__)

# weight formats that are not needed (only safetensors are downloaded, when the repository has them)
_EXCLUDED_FORMATS = [
    "*.onnx",
    "onnx/*",
    "*.h5",
    "*.msgpack",
    "*.ot",
    "flax_model*",
    "tf_model*",
]

Pipeline = Callable[
    [str], list[dict[str, Any]]
]  # text -> [{start, end, entity_group, score}]


def _is_local(model: str) -> bool:
    return Path(model).is_absolute() or model.startswith(".")


def model_dir(cfg: Ner) -> str:
    """Folder of the model in the cache (or the folder given in the configuration), without network.

    Raises:
        DetectorUnavailable: The library or the model is missing.
    """
    if _is_local(cfg.model):
        if not Path(cfg.model).is_dir():
            raise DetectorUnavailable(f"NER model folder '{cfg.model}' does not exist")
        return cfg.model
    if not NER_INSTALLED:
        raise DetectorUnavailable(
            "transformers/torch not installed (image without the 'ner' extra)"
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
            f"NER model '{cfg.model}' missing in the cache (download it with `just models`): {type(exc).__name__}"
        ) from exc
    if not any(f.suffix in (".safetensors", ".bin") for f in Path(folder).iterdir()):
        raise DetectorUnavailable(
            f"weights of the NER model '{cfg.model}' missing in the cache"
        )
    return folder


def download(cfg: Ner) -> str:
    """Download the model into the cache (only safetensors when available); return its folder.

    Raises:
        DetectorUnavailable: The 'ner' extra is not installed.
    """
    try:
        return model_dir(cfg)
    except DetectorUnavailable:
        if not NER_INSTALLED or _is_local(cfg.model):
            raise
    files = list_repo_files(cfg.model, revision=cfg.revision)
    excluded = _EXCLUDED_FORMATS + (
        ["*.bin"] if any(f.endswith(".safetensors") for f in files) else []
    )
    logger.warning(
        "NER model '%s' missing: downloading into the Hugging Face cache", cfg.model
    )
    return snapshot_download(cfg.model, revision=cfg.revision, ignore_patterns=excluded)


def _load_pipeline(cfg: Ner) -> Pipeline:
    folder = model_dir(cfg)
    if cfg.threads and torch.get_num_threads() != cfg.threads:
        # with every core inference is much slower because of thread contention
        torch.set_num_threads(cfg.threads)
    return hf_pipeline(
        "token-classification",
        model=folder,
        aggregation_strategy="simple",
        device="cpu",
    )


# loaded models, shared by the documents of the process: key -> (pipeline, lock) or error message
_SHARED: dict[
    tuple[str, str | None, int | None], tuple[Pipeline, threading.Lock] | str
] = {}
_SHARED_LOCK = threading.Lock()


def _shared(cfg: Ner) -> tuple[Pipeline, threading.Lock]:
    """Model loaded once per process; a loading error is remembered (no retry at every page)."""
    key = (cfg.model, cfg.revision, cfg.threads)
    with _SHARED_LOCK:
        if key not in _SHARED:
            try:
                _SHARED[key] = (_load_pipeline(cfg), threading.Lock())
            except DetectorUnavailable as exc:
                _SHARED[key] = str(exc)
            except (
                Exception
            ) as exc:  # any loading failure disables the detector, with a warning
                _SHARED[key] = (
                    f"NER model '{cfg.model}' cannot be loaded: {type(exc).__name__}"
                )
        value = _SHARED[key]
    if isinstance(value, str):
        raise DetectorUnavailable(value)
    return value


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
    name = "ner"

    def __init__(self, cfg: Ner, pipeline: Pipeline | None = None) -> None:
        """Build the detector.

        Args:
            cfg: NER configuration.
            pipeline: For tests; by default the shared model of the process, loaded at first use.
        """
        self.cfg = cfg
        self.label_map = {k.upper(): v for k, v in cfg.label_map.items()}
        self._own = (pipeline, threading.Lock()) if pipeline is not None else None
        reg = registry()
        self.person_categories = {
            c for c in reg.descriptions if reg.family(c) == "PERSONA"
        }

    def detect(self, text: str) -> list[Proposal]:
        if not text.strip():
            return []
        model, lock = self._own or _shared(self.cfg)
        best: dict[tuple[int, int, str], float] = {}
        with lock:  # the model is not thread-safe: one page at a time
            for s0, e0 in windows(text, self.cfg.window_words, self.cfg.overlap_words):
                for item in model(text[s0:e0]):
                    category = self.label_map.get(str(item["entity_group"]).upper())
                    if category and float(item["score"]) >= self.cfg.threshold:
                        key = (s0 + int(item["start"]), s0 + int(item["end"]), category)
                        best[key] = max(best.get(key, 0.0), float(item["score"]))
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
        """Discard labels and common words mistaken for names (start None if the value is ignored)."""
        f = self.cfg.filters
        false_positives = {p.lower() for p in f.false_positives}
        value = text[s:e].strip()
        s = s + text[s:e].find(value) if value else s
        e = s + len(value)
        # inner newlines: they often join label and value
        if "\n" in value or len(value) < f.min_length:
            return None, e, category
        tokens = [t for t in re.split(r"\s+", value) if t]
        if not tokens or all(
            t.lower().strip(".:,*|") in false_positives for t in tokens
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
            while tokens and tokens[0].lower().strip(".:,") in false_positives:
                tokens = tokens[1:]
            if not tokens:
                return None, e, category
            s = s + value.find(tokens[0])
            if e - s < f.min_length:
                return None, e, category
        return s, e, category
