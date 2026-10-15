"""Validation run: every profile on the filtered dataset, then the report.

Usage: ``uv run python -m anonval.run [--config FILE] [--profilo NAME ...] [--output DIR]``
(``just validation``). Exit code: 0 every profile passed, 1 a profile failed, 2 the run did not
start (configuration, dataset or service unavailable).

Writes ``<output>/<timestamp>/``: ``report.md``, ``metriche.json`` and, per profile,
``raw/<profilo>/<id>.json`` with the final status of every job (counters, boxes, no values).
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console

from anonval.client import ApiError, ServiceClient
from anonval.locate import page_texts
from anonval.metrics import as_dict, summarize, verdict
from anonval.report import ProfileRun, pct, render_report
from anonval.scoring import DocScore, active_categories, score_document
from anonval.settings import Config, Profile, load_config

ROOT = Path(__file__).resolve().parents[1]
console = Console()


def load_dataset(config: Config) -> list[dict[str, Any]]:
    """Manifest entries selected by the dataset filters of the configuration.

    Raises:
        ValueError: Missing manifest or no document selected.
    """
    root = Path(config.dataset.percorso)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"manifest non leggibile in {root}") from exc
    d = config.dataset
    docs = [
        e
        for e in manifest["documents"]
        if (not d.tipi or e["type"] in d.tipi)
        and (not d.difficolta or e["difficulty"] in d.difficolta)
        and (not d.documenti or e["id"] in d.documenti)
    ]
    if not docs:
        raise ValueError("nessun documento selezionato dai filtri del dataset")
    return docs


def run_profile(
    client: ServiceClient,
    config: Config,
    profile: Profile,
    docs: list[dict[str, Any]],
    raw: Path | None = None,
    on_document: Callable[[DocScore], None] | None = None,
) -> ProfileRun:
    """Every document of the dataset with the parameters of ``profile``."""
    root = Path(config.dataset.percorso)
    params = config.effective(profile)
    active = active_categories(client.categories(), params.enable, params.disable)
    scores: list[DocScore] = []
    for entry in docs:
        pdf = (root / entry["pdf"]).read_bytes()
        gold = json.loads((root / entry["ground_truth"]).read_text(encoding="utf-8"))
        try:
            result = client.run(f"{entry['id']}.pdf", pdf, params.form())
        except ApiError as exc:
            score = DocScore(
                entry["id"], entry["type"], entry["difficulty"], "error", error=str(exc)
            )
        else:
            released = page_texts(result.pdf) if result.pdf else None
            score = score_document(
                gold, page_texts(pdf), result.status, active, released, result.markdown
            )
            if raw is not None:
                raw.mkdir(parents=True, exist_ok=True)
                (raw / f"{entry['id']}.json").write_text(
                    json.dumps(result.status, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        scores.append(score)
        if on_document is not None:
            on_document(score)
    return ProfileRun(profile, scores, summarize(scores))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validazione dell'anonimizzazione.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--profilo", nargs="*", default=[], help="solo questi profili")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        docs = load_dataset(config)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    profiles = [p for p in config.profili if not args.profilo or p.nome in args.profilo]
    if not profiles:
        console.print("[red]nessun profilo selezionato[/red]")
        return 2
    s = config.server
    client = ServiceClient(s.base_url, s.poll_interval_s, s.timeout_documento_s)
    if not client.ready():
        console.print(f"[red]servizio non pronto su {s.base_url}[/red]")
        return 2
    out = args.output / time.strftime("%Y%m%d-%H%M%S")
    runs = []
    for profile in profiles:
        with console.status(f"profilo {profile.nome}") as status:
            done = 0

            def progress(score: DocScore, name: str = profile.nome) -> None:
                nonlocal done
                done += 1
                status.update(f"profilo {name}: {done}/{len(docs)} {score.id}")

            run = run_profile(
                client, config, profile, docs, out / "raw" / profile.nome, progress
            )
        runs.append(run)
        failed = verdict(run.summary, config.soglie)
        console.print(
            f"{profile.nome}: {'[red]FAIL[/red]' if failed else '[green]PASS[/green]'} "
            f"recall {pct(run.summary.recall)}, fuga {pct(run.summary.leak_rate)}, "
            f"precisione {pct(run.summary.precision)}"
        )
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render_report(runs, config), encoding="utf-8")
    metrics = {r.profile.nome: as_dict(r.summary, config.soglie) for r in runs}
    (out / "metriche.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    console.print(f"report: {out / 'report.md'}")
    return 1 if any(verdict(r.summary, config.soglie) for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
