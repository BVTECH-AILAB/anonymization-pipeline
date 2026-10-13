"""Dataset build: specs -> native PDFs + ground truth + manifest, with blocking checks.

Usage: ``uv run python -m anonval.build [--dataset DIR] [--parziale] [--solo ID ...]``
(``just validation-dataset``).
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pydantic import ValidationError
from reportlab.platypus.doctemplate import LayoutError
from rich.console import Console

from anonval.render import render_pdf
from anonval.spec import DIFFICULTIES, DOC_TYPES, Mention, Spec, resolve
from anonval.text import find
from anonymization_pipeline.config import Settings
from anonymization_pipeline.ingest import load_pdf
from anonymization_pipeline.pipeline_config import load_pipeline_config
from anonymization_pipeline.pipeline_config.categories import registry

DATASET = Path(__file__).resolve().parents[1] / "dataset"
PER_DIFFICULTY = 4
MASK = "#"
# rubric of the generation prompt: pages and distinct identifying values (health and special
# categories excluded) per difficulty
PAGES = {"facile": (1, 1), "media": (2, 3), "complessa": (3, 5)}
IDENTIFYING = {"facile": (8, 18), "media": (15, 35), "complessa": (30, 200)}
NOT_IDENTIFYING = {"sanitari", "particolari"}

console = Console()


def page_texts(pdf: bytes) -> list[str]:
    """Text layer of every page, as PDFium extracts it."""
    doc = pdfium.PdfDocument(pdf)
    try:
        texts = []
        for page in doc:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return texts
    finally:
        doc.close()


def ground_truth(
    spec: Spec, mentions: list[Mention], texts: list[str], masked: str = ""
) -> dict[str, Any]:
    """Ground truth of a document: one entry per (value, category).

    Args:
        spec: The spec.
        mentions: Its markers, in document order.
        texts: Text layer of every page of the PDF.
        masked: Text of the PDF drawn with the marked values masked. ``unmarked`` counts the
            copies of a value left there: the same string used as plain text ("rosa" the colour,
            "Rosa" the name), which the scoring tolerates in the redacted output.

    Raises:
        ValueError: A value is missing from the text layer of the PDF.
    """
    reg = registry()
    group_of = {c: g for g, cats in reg.groups.items() for c in cats}
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for m in mentions:
        entry = entries.setdefault(
            (m.value, m.category),
            {
                "value": m.value,
                "category": m.category,
                "group": group_of[m.category],
                "family": reg.family(m.category),
                "mentions": 0,
                "locations": [],
            },
        )
        entry["mentions"] += 1
        if m.location not in entry["locations"]:
            entry["locations"].append(m.location)
    missing = []
    for entry in entries.values():
        entry["pages"] = [
            i + 1 for i, text in enumerate(texts) if find(text, entry["value"])
        ]
        entry["unmarked"] = len(find(masked, entry["value"])) if masked else 0
        if not entry["pages"]:
            missing.append(f"{entry['category']}:{entry['value']}")
    if missing:
        raise ValueError(f"values missing from the PDF text: {', '.join(missing)}")
    return {
        "id": spec.id,
        "type": spec.type,
        "difficulty": spec.difficulty,
        "pages": len(texts),
        "entities": list(entries.values()),
        "negatives": [n.model_dump() for n in spec.negatives],
    }


def rubric_problems(difficulty: str, gold: dict[str, Any]) -> list[str]:
    """Pages and identifying values outside the range of the difficulty."""
    problems = []
    low, high = PAGES[difficulty]
    if not low <= gold["pages"] <= high:
        problems.append(f"{gold['pages']} pagine, attese {low}-{high}")
    n = sum(1 for e in gold["entities"] if e["group"] not in NOT_IDENTIFYING)
    low, high = IDENTIFYING[difficulty]
    if not low <= n <= high:
        problems.append(f"{n} valori identificativi distinti, attesi {low}-{high}")
    return problems


def native_problems(pdf: bytes) -> list[str]:
    """Pages the service would not treat as native (reason per page)."""
    settings = Settings(
        _env_file=None, environment="local", default_extraction="docling"
    )
    cfg = load_pipeline_config(settings)
    doc = load_pdf(pdf, cfg)
    return [f"pagina {p.index + 1}: {p.reason}" for p in doc.pages if not p.native]


def build_one(path: Path, dataset: Path) -> dict[str, Any]:
    """Spec file -> PDF and ground truth files; manifest entry.

    Raises:
        ValueError: Invalid spec, value not extractable, page not native.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        spec = Spec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    if path.stem != spec.id:
        raise ValueError(f"file name {path.name} differs from id {spec.id}")
    resolved, mentions = resolve(spec)
    pdf = render_pdf(resolved)
    masked = "\n".join(page_texts(render_pdf(resolve(spec, mask=MASK)[0])))
    gold = ground_truth(spec, mentions, page_texts(pdf), masked)
    rubric = rubric_problems(spec.difficulty, gold)
    if rubric:
        raise ValueError("; ".join(rubric))
    if problems := native_problems(pdf):
        raise ValueError(f"pages not native: {'; '.join(problems)}")
    gold["sha256"] = hashlib.sha256(pdf).hexdigest()
    pdf_path = dataset / "pdf" / spec.type / f"{spec.id}.pdf"
    gold_path = dataset / "ground_truth" / spec.type / f"{spec.id}.json"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf)
    gold_path.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "id": spec.id,
        "type": spec.type,
        "difficulty": spec.difficulty,
        "pages": gold["pages"],
        "entities": len(gold["entities"]),
        "pdf": pdf_path.relative_to(dataset).as_posix(),
        "ground_truth": gold_path.relative_to(dataset).as_posix(),
    }


def count_problems(entries: list[dict[str, Any]]) -> list[str]:
    """Missing or extra documents per type and difficulty."""
    counts = Counter((e["type"], e["difficulty"]) for e in entries)
    return [
        f"{t}/{d}: {counts[(t, d)]} documenti invece di {PER_DIFFICULTY}"
        for t in DOC_TYPES
        for d in DIFFICULTIES
        if counts[(t, d)] != PER_DIFFICULTY
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera PDF e ground truth dalle spec."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument(
        "--parziale",
        action="store_true",
        help="non verifica 4 documenti per tipo e difficolta",
    )
    parser.add_argument(
        "--solo",
        nargs="*",
        default=[],
        help="solo questi id (il manifest non viene riscritto)",
    )
    args = parser.parse_args(argv)
    dataset: Path = args.dataset
    specs = sorted((dataset / "specs").glob("*/*.json"))
    if args.solo:
        specs = [p for p in specs if p.stem in args.solo]
    if not specs:
        console.print(f"[red]Nessuna spec in {dataset / 'specs'}[/red]")
        return 1
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    with console.status("Generazione...") as status:
        for i, path in enumerate(specs, start=1):
            status.update(f"[{i}/{len(specs)}] {path.stem}")
            try:
                entries.append(build_one(path, dataset))
            except (ValueError, LayoutError) as exc:
                errors.append(f"{path.stem}: {exc}")
                console.print(f"[red]ERRORE[/red] {path.stem}: {exc}")
            else:
                e = entries[-1]
                console.print(
                    f"[green]OK[/green] {e['id']} ({e['pages']} pag., {e['entities']} entita)"
                )
    if not args.solo:
        if not args.parziale:
            errors += count_problems(entries)
        (dataset / "manifest.json").write_text(
            json.dumps(
                {"documents": sorted(entries, key=lambda e: e["id"])},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if errors:
        console.print(f"\n[red]{len(errors)} problemi:[/red]")
        for error in errors:
            console.print(f"  - {error}")
        return 1
    console.print(f"\n[green]{len(entries)} documenti generati in {dataset}[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
