"""Evaluation CLI: score the engine against the ground truth of the dataset.

    uv run python -m anonymization_eval evaluate --dataset dataset
    uv run python -m anonymization_eval evaluate --variants scan --split test --limit 20
    uv run python -m anonymization_eval compare reports_a reports_b

Output goes to stderr through logging (``print`` is banned by ruff); the
machine-readable results are written to files.
"""

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from anonymization_eval import evaluate as evaluation
from anonymization_eval.config import EvalSettings, get_settings
from anonymization_eval.log import setup_logging

logger = logging.getLogger("anonymization_eval")
app = typer.Typer(help="Evaluation of the anonymization engine.", no_args_is_help=True)

# `just ocr-server` and `just start` serve the OCR API here
LOCAL_OCR_SERVER = "http://127.0.0.1:8829"


def eval_settings(ocr_server_url: str | None) -> EvalSettings:
    """Settings of an evaluation run: the OCR server from the option, else
    ``ANONPIP_OCR_SERVER_URL``, else the local one (``.env.example`` leaves it empty).
    """
    settings = get_settings()
    url = ocr_server_url or settings.ocr_server_url or LOCAL_OCR_SERVER
    return settings.model_copy(update={"ocr_server_url": url})


@app.callback()
def _setup() -> None:
    setup_logging("INFO", stream=sys.stderr)


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
    ocr_server_url: Annotated[
        str | None,
        typer.Option(
            help=f"OCR server (default: ANONPIP_OCR_SERVER_URL, else {LOCAL_OCR_SERVER})."
        ),
    ] = None,
) -> None:
    """Score the pipeline against the ground truth -> <out>/evaluation.md."""
    if split not in ("all", "dev", "test"):
        raise typer.BadParameter("split must be all, dev or test")
    settings = eval_settings(ocr_server_url)
    reports = [
        evaluation.evaluate(dataset, v, out, limit, split=split, settings=settings)
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
def compare(
    baseline: Annotated[Path, typer.Argument(help="Report folder of the baseline.")],
    candidate: Annotated[Path, typer.Argument(help="Report folder to compare.")],
    out: Annotated[Path, typer.Option(help="Markdown file.")] = Path(
        "reports/comparison.md"
    ),
) -> None:
    """Side by side of two evaluations: deltas per variant, per type, worst documents."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(evaluation.compare(baseline, candidate))
    logger.info("Comparison: %s", out)


if __name__ == "__main__":
    app()
