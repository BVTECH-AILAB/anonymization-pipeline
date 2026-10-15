"""Developer tools: synthetic dataset, single-file anonymization, evaluation, OCR benchmark.

    uv run python -m tools generate --n 21 --out dataset
    uv run python -m tools generate --n 7 --out dataset --append
    uv run python -m tools anonymize dataset/scan/000_lettera_dimissione.pdf
    uv run python -m tools evaluate --dataset dataset
    uv run python -m tools bench-ocr --engines doctr,lighton --limit 3
    uv run python -m tools preview file.pdf

Output goes to stderr through logging (``print`` is banned by ruff); the
machine-readable results are written to files.
"""

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Annotated

import pymupdf
import typer

from anonmyzation_pipeline.config import get_settings
from anonmyzation_pipeline.logging_setup import setup_logging
from anonmyzation_pipeline.main import build_pipeline
from anonmyzation_pipeline.services.anonymization.redact import redact
from anonmyzation_pipeline.services.anonymization.verify import verify
from tools import bench_ocr
from tools import evaluate as evaluation
from tools.dataset.generate import generate as generate_dataset
from tools.dataset.generate import next_index

logger = logging.getLogger("tools")
app = typer.Typer(help="Anonymization pipeline developer tools.", no_args_is_help=True)


@app.callback()
def _setup() -> None:
    setup_logging(
        get_settings().model_copy(update={"log_level": "INFO"}), stream=sys.stderr
    )


@app.command()
def generate(
    n: Annotated[
        int,
        typer.Option(help="Number of documents to create (cycled over the 7 types)."),
    ] = 21,
    out: Annotated[Path, typer.Option(help="Output folder.")] = Path("dataset"),
    seed: Annotated[int, typer.Option(help="Random seed.")] = 7,
    no_scans: Annotated[bool, typer.Option(help="Only native PDFs.")] = False,
    append: Annotated[
        bool,
        typer.Option("--append", help="Add N new documents after the existing ones."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Delete the folder content and regenerate."),
    ] = False,
) -> None:
    """Synthetic dataset: native, scanned and scanned+OCR-layer PDFs with separate ground truth."""
    if append and overwrite:
        raise typer.BadParameter("use either --append or --overwrite, not both")
    start = next_index(out) if out.exists() else 0
    if start and not (append or overwrite):
        logger.error(
            "%s already contains %d documents: use --append or --overwrite", out, start
        )
        raise typer.Exit(1)
    if overwrite and out.exists():
        for sub in ("native", "scan", "scan_ocr", "ground_truth"):
            shutil.rmtree(out / sub, ignore_errors=True)
        start = 0
    files = generate_dataset(n, out, seed=seed, scans=not no_scans, start=start)
    logger.info(
        "%d PDF written to %s (documents %03d-%03d)",
        len(files),
        out,
        start,
        start + n - 1,
    )


@app.command()
def anonymize(
    pdf: Path,
    out: Annotated[
        Path | None, typer.Option("-o", help="Output PDF (default: <name>.anon.pdf).")
    ] = None,
) -> None:
    """Anonymize one PDF and verify it (exit code 2 when verification fails)."""
    out = out or pdf.with_suffix(".anon.pdf")
    settings = get_settings()
    pipeline = build_pipeline(settings)
    analysis = pipeline.analyze(pdf)
    redact(pdf, analysis, out, style=settings.redaction_style)
    report = verify(pdf, out, analysis, tol=settings.verify_pixel_tolerance)
    summary = {
        "output": str(out),
        "counts": analysis.counts(),
        "timings": analysis.timings,
        "verified": report.ok,
    }
    out.with_suffix(".json").write_text(
        json.dumps(summary | {"issues": report.issues}, indent=1, ensure_ascii=False)
    )
    logger.info("anonymized: %s", json.dumps(summary))
    if not report.ok:
        raise typer.Exit(2)


@app.command()
def evaluate(
    dataset: Annotated[Path, typer.Option(help="Dataset folder.")] = Path("dataset"),
    variants: Annotated[
        str, typer.Option(help="Comma-separated variants.")
    ] = "native,scan,scan_ocr",
    out: Annotated[Path, typer.Option(help="Report folder.")] = Path("reports"),
    limit: Annotated[
        int | None, typer.Option(help="Only the first N documents.")
    ] = None,
) -> None:
    """Score the pipeline against the ground truth -> reports/evaluation.md."""
    reports = [evaluation.evaluate(dataset, v, out, limit) for v in variants.split(",")]
    (out / "evaluation.md").write_text(evaluation.to_markdown(reports))
    for r in reports:
        logger.info(
            "%s: recall=%s precision=%s f1=%s ap50=%s map50=%s verified=%s s/page=%s",
            r["variant"],
            r["box"]["recall"],
            r["box"]["precision"],
            r["box"]["f1"],
            r["ap_pii_50"],
            r["map_50"],
            r["verified_documents"],
            r["seconds_per_page"],
        )
    logger.info("Full report: %s", out / "evaluation.md")


@app.command("bench-ocr")
def bench(
    dataset: Annotated[Path, typer.Option(help="Dataset folder.")] = Path("dataset"),
    engines: Annotated[
        str, typer.Option(help="doctr, doctr_fast, lighton, lighton_1b")
    ] = "doctr,doctr_fast",
    limit: Annotated[
        int | None, typer.Option(help="Only the first N documents.")
    ] = None,
    out: Annotated[Path, typer.Option(help="Report folder.")] = Path("reports"),
) -> None:
    """Compare OCR engines on the scanned variant -> reports/ocr_benchmark.json."""
    report = bench_ocr.run(dataset, engines.split(","), limit, out)
    logger.info("OCR benchmark: %s", json.dumps(report))


@app.command()
def preview(
    pdf: Path,
    out: Annotated[
        Path | None, typer.Option(help="Folder for PNGs (default: next to the PDF).")
    ] = None,
    dpi: int = 110,
) -> None:
    """Render a PDF to PNG pages."""
    out = out or pdf.with_suffix("")
    out.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            page.get_pixmap(dpi=dpi).save(out / f"p{i + 1}.png")
    logger.info("PNG pages in %s", out)


if __name__ == "__main__":
    app()
