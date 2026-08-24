"""Scoring against planted ground truth. Guide §7.3.

    truth.json + the agent's public output -> marksheet

This module reads exactly two things: the answer key, and the `RunResult` the
agent returned. It never imports a matcher, a context, or any pipeline internal
— that is the gate 3 stop condition, and it is what makes the score mean
something. `build_pipeline()` is the one seam, and everything past it is opaque.

    make eval                     # seed 42, scale 500
    make eval SCALE=5000          # throughput run
    make eval NO_LLM=1            # ablation: the deterministic core alone
    make eval SEEDS=1,2,3,4,5     # variance across seeds
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import Settings
from core.console import configure_stdout
from core.run_result import RunResult
from eval.metrics import BUCKET_EDGES, Bucket, Metrics, bucket_of, render, render_multi_seed
from pipeline.factory import build_pipeline

RUNS_DIR = Path("eval") / "runs"


class TruthVersionError(RuntimeError):
    """The dataset was built by an incompatible generator (§6.3, "Versioned")."""


def load_truth(dataset: Path) -> dict[str, Any]:
    """Read the answer key, refusing a dataset from a different major version."""
    from generator.writers import GENERATOR_VERSION

    truth = json.loads((dataset / "truth.json").read_text(encoding="utf-8"))
    theirs = str(truth.get("generator_version", "0.0.0")).split(".")[0]
    ours = GENERATOR_VERSION.split(".")[0]
    if theirs != ours:
        raise TruthVersionError(
            f"{dataset} was built by generator v{truth.get('generator_version')}, "
            f"this eval expects v{ours}.x — regenerate it rather than scoring "
            "against a stale answer key"
        )
    return truth


def is_correct(outcome_ids: frozenset[str], truth_ids: list[str]) -> bool:
    """THE scoring rule. Exact set equality — §7.3.

    Two of three orders correct is WRONG, not 67% right: a half-matched
    settlement posts a wrong journal entry, and there is no partial credit in
    reconciliation. Any overlap ratio or fuzzy comparison here would inflate
    precision and make the headline number meaningless.
    """
    if not outcome_ids or not truth_ids:
        # An empty claim against an unmapped credit would otherwise compare
        # equal (set() == set()) and count as correct. A match that explains
        # nothing is never right.
        return False
    return set(outcome_ids) == set(truth_ids)


def score(
    truth: dict[str, Any],
    result: RunResult,
    *,
    settings: Settings,
    dataset: str = "",
    no_llm: bool = False,
    elapsed_s: float = 0.0,
) -> Metrics:
    """Turn an answer key plus a run's public output into a marksheet."""
    mappings: dict[str, list[str]] = truth["mappings"]

    total = len(mappings)
    attempted = len(result.matches)
    correct = sum(
        1
        for utr, m in result.matches.items()
        if is_correct(m.ledger_ids, mappings.get(utr, []))
    )

    # A credit the answer key does not map (an orphan, say) that the agent
    # nonetheless claims to have explained is a false positive, and is already
    # counted as incorrect above via `.get(utr, [])`.
    false_positives = tuple(
        sorted(utr for utr in result.matches if utr not in mappings)
    )

    planted_by_ref = {e["ref"]: e["type"] for e in truth["exceptions"]}
    planted = set(planted_by_ref)
    caught = {e.ref for e in result.exceptions}
    missed = tuple(
        (ref, planted_by_ref[ref]) for ref in sorted(planted - caught)
    )

    tallies = {label: [0, 0] for label, _, _ in BUCKET_EDGES}
    for utr, m in result.matches.items():
        entry = tallies[bucket_of(m.confidence)]
        entry[0] += 1
        entry[1] += int(is_correct(m.ledger_ids, mappings.get(utr, [])))
    calibration = tuple(
        Bucket(label=label, count=tallies[label][0], correct=tallies[label][1])
        for label, _, _ in BUCKET_EDGES
    )

    auto_posted = result.auto_posted(settings.auto_post_threshold)

    return Metrics(
        dataset=dataset,
        seed=int(truth.get("seed", -1)),
        scale=int(truth.get("scale", 0)),
        no_llm=no_llm,
        total=total,
        attempted=attempted,
        correct=correct,
        match_rate=attempted / total if total else 0.0,
        match_precision=correct / attempted if attempted else 0.0,
        planted=len(planted),
        caught=len(planted & caught),
        exception_recall=len(planted & caught) / len(planted) if planted else 0.0,
        missed=missed,
        false_positives=false_positives,
        auto_posted=auto_posted,
        auto_resolution=auto_posted / total if total else 0.0,
        llm_calls=result.llm_calls,
        llm_cost_paise=result.llm_cost_paise,
        cost_per_100_paise=(result.llm_cost_paise / total * 100) if total else 0.0,
        calibration=calibration,
        elapsed_s=elapsed_s,
        throughput=(total / elapsed_s) if elapsed_s > 0 else 0.0,
        layer_timings_ms=dict(result.layer_timings_ms),
    )


def ensure_dataset(dataset: Path, seed: int, scale: int, *, regenerate: bool = True) -> Path:
    """Guarantee `dataset` holds a seed/scale dataset matching what was asked for.

    Generating when absent makes `make eval` work from a clean clone with no
    prior step, which is what gate 14 asks of a judge's machine.

    Regenerating on MISMATCH matters more. `data/seed42` is reused across scales,
    so `make eval SCALE=500` would otherwise silently score whatever scale
    happened to be on disk and report the wrong denominator — a measurement bug
    that looks like a real result. Pass regenerate=False to score exactly what
    is there.
    """
    from generator.generate import generate

    truth_path = dataset / "truth.json"
    if not truth_path.exists():
        print(f"  (no dataset at {dataset} — generating seed {seed} scale {scale})")
        generate(seed, scale, dataset)
        return dataset

    existing = json.loads(truth_path.read_text(encoding="utf-8"))
    on_disk = (int(existing.get("seed", -1)), int(existing.get("scale", -1)))
    if regenerate and on_disk != (seed, scale):
        print(
            f"  (dataset at {dataset} is seed {on_disk[0]} scale {on_disk[1]}, "
            f"asked for seed {seed} scale {scale} — regenerating)"
        )
        generate(seed, scale, dataset)
    return dataset


def evaluate(
    dataset: Path,
    *,
    no_llm: bool = False,
    no_fee_model: bool = False,
    settings: Settings | None = None,
) -> Metrics:
    """Run the agent over a dataset and score what it returns."""
    settings = settings or Settings()
    truth = load_truth(dataset)

    agent = build_pipeline(no_llm=no_llm, no_fee_model=no_fee_model)

    t0 = time.perf_counter()
    result = agent.run(dataset)
    elapsed = time.perf_counter() - t0

    return score(
        truth,
        result,
        settings=settings,
        dataset=str(dataset),
        no_llm=no_llm,
        elapsed_s=elapsed,
    )


def snapshot(metrics: Metrics) -> Path:
    """Dump the run for regression tracking (§7.5)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{stamp}-seed{metrics.seed}-scale{metrics.scale}.json"
    path.write_text(
        json.dumps(metrics.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval.evaluate",
        description="Score the agent against planted ground truth.",
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scale", type=int, default=500)
    parser.add_argument("--seeds", type=str, default="", help="e.g. 1,2,3,4,5")
    parser.add_argument("--no-llm", action="store_true", help="ablation: deterministic core only")
    parser.add_argument("--no-fee-model", action="store_true", help="ablation: disable L2")
    parser.add_argument(
        "--no-timing",
        action="store_true",
        help="omit wall-clock lines so two runs diff byte-identically",
    )
    parser.add_argument("--json", action="store_true", help="emit metrics as JSON")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument(
        "--no-regenerate",
        action="store_true",
        help="score the dataset exactly as it is on disk, even on a seed/scale mismatch",
    )
    args = parser.parse_args(argv)

    configure_stdout()

    settings = Settings()

    if args.seeds:
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
        runs = []
        for seed in seeds:
            ds = ensure_dataset(
                Path("data") / f"seed{seed}", seed, args.scale,
                regenerate=not args.no_regenerate,
            )
            runs.append(evaluate(ds, no_llm=args.no_llm,
                                 no_fee_model=args.no_fee_model, settings=settings))
        print(render_multi_seed(runs))
        return 0

    dataset = args.dataset or Path("data") / f"seed{args.seed}"
    ensure_dataset(dataset, args.seed, args.scale, regenerate=not args.no_regenerate)
    metrics = evaluate(
        dataset,
        no_llm=args.no_llm,
        no_fee_model=args.no_fee_model,
        settings=settings,
    )

    if args.json:
        print(json.dumps(metrics.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(render(metrics, show_timing=not args.no_timing))

    if not args.no_snapshot:
        snapshot(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
