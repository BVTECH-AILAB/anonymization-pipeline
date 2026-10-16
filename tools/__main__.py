"""Developer tools: synthetic dataset, single-file anonymization, evaluation, viewer.

    uv run python -m tools generate --n 7 --out dataset --append
    uv run python -m tools generate --n 21 --out dataset --overwrite
    uv run python -m tools anonymize dataset/scan/000_lettera_dimissione.pdf
    uv run python -m tools evaluate --dataset dataset
    uv run python -m tools viewer
    uv run python -m tools preview file.pdf

Output goes to stderr through logging (``print`` is banned by ruff); the
machine-readable results are written to files.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import pymupdf
import typer
import uvicorn

from anonmyzation_pipeline.config import get_settings
from anonmyzation_pipeline.logging_setup import setup_logging
from anonmyzation_pipeline.main import build_pipeline
from anonmyzation_pipeline.services.anonymization.release import redact_and_verify
from tools import evaluate as evaluation
from tools import viewer as eval_viewer
from tools.dataset.generate import generate as generate_dataset
from tools.dataset.generate import next_index, remove_documents

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
        typer.Option(
            "--overwrite",
            help="Delete the generated documents (NNN_*) and regenerate; other sources are kept.",
        ),
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
        remove_documents(out, "[0-9][0-9][0-9]_*")
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
    release = redact_and_verify(
        pdf,
        analysis,
        out,
        style=settings.redaction_style,
        tol=settings.verify_pixel_tolerance,
    )
    report = release.report
    summary = {
        "output": str(out),
        "counts": analysis.counts(),
        "timings": analysis.timings | release.seconds,
        "verified": report.ok,
        "flattened_pages": [p + 1 for p in release.flattened],
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
    split: Annotated[
        str,
        typer.Option(
            help="all, dev or test: test (about 30% of the template families) is never used for tuning."
        ),
    ] = "all",
) -> None:
    """Score the pipeline against the ground truth -> reports/evaluation.md."""
    if split not in ("all", "dev", "test"):
        raise typer.BadParameter("split must be all, dev or test")
    reports = [
        evaluation.evaluate(dataset, v, out, limit, split=split)
        for v in variants.split(",")
    ]
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


@app.command()
def viewer(
    dataset: Annotated[Path, typer.Option(help="Dataset folder.")] = Path("dataset"),
    out: Annotated[Path, typer.Option(help="Report folder.")] = Path("reports"),
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1.")] = 8765,
) -> None:
    """Evaluation viewer: ground truth vs predictions, document by document, updated live."""

    def run(variant: str) -> None:
        evaluation.evaluate(dataset, variant, out)
        reports = [
            json.loads((out / f"eval_{v}.json").read_text())
            for v in ("native", "scan", "scan_ocr")
            if (out / f"eval_{v}.json").is_file()
        ]
        (out / "evaluation.md").write_text(evaluation.to_markdown(reports))

    logger.info("Evaluation viewer on http://127.0.0.1:%d", port)
    uvicorn.run(
        eval_viewer.create_app(dataset, out, run),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


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
