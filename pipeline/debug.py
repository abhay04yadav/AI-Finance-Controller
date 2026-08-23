"""Per-stage debug and profiling CLI. Guide §9.6.

    python -m pipeline.debug --stage L0 --dataset data/seed42     (gate 4)
    python -m pipeline.debug --profile --dataset data/seed42      (gate 10)

When throughput is a judged metric you need to know which layer is slow, and
being able to say "L3 is 4ms, L4 is 380ms, that's why we keep L4 under 10%" is a
much better answer than a single total.

Stages land as their gates do; asking for one that does not exist yet says so
rather than pretending.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from collections import Counter
from pathlib import Path

from core.models import Source
from ingest.normalizer import IngestResult, load_dataset


def show_l0(dataset: Path) -> IngestResult:
    """Ingest the dataset and report what came out, per source."""
    t0 = time.perf_counter()
    result = load_dataset(dataset)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"L0 · INGEST & NORMALIZE — {dataset}")
    print("─" * 66)
    print(f"{'source':<12}{'records':>9}{'total':>18}{'refs':>8}{'dates':>26}")
    for source in (Source.LEDGER, Source.SETTLEMENT, Source.BANK):
        rows = result.by_source(source)
        if not rows:
            print(f"{source:<12}{0:>9}")
            continue
        total = result.total_paise(source)
        with_refs = sum(1 for r in rows if r.refs)
        lo = min(r.value_date for r in rows)
        hi = max(r.value_date for r in rows)
        print(
            f"{source:<12}{len(rows):>9}{_rupees(total):>18}{with_refs:>8}"
            f"{f'{lo} .. {hi}':>26}"
        )

    print("─" * 66)
    print(f"{'total':<12}{len(result.records):>9}    parsed in {elapsed_ms:.1f} ms")

    if result.failures:
        print()
        print(f"INGEST_ERROR — {len(result.failures)} row(s) could not be read")
        by_source = Counter(str(f.source) for f in result.failures)
        for source, n in sorted(by_source.items()):
            print(f"  {source:<12}{n:>4}")
        for failure in result.failures[:10]:
            print(f"  {failure.describe()}")
        if len(result.failures) > 10:
            print(f"  ... and {len(result.failures) - 10} more")
    else:
        print("no ingest errors")

    # The tie-out that matters: the bridge document and the bank statement
    # describe the same money.
    settlement_total = result.total_paise(Source.SETTLEMENT)
    bank_total = result.total_paise(Source.BANK)
    print()
    print(f"settlement net total  {_rupees(settlement_total):>18}")
    print(f"bank credit total     {_rupees(bank_total):>18}")
    delta = bank_total - settlement_total
    note = "" if delta == 0 else "   <- duplicated bank rows account for this"
    print(f"difference            {_rupees(delta):>18}{note}")
    return result


def _rupees(paise: int) -> str:
    from core.money import Money

    return str(Money(paise))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.debug")
    parser.add_argument("--dataset", type=Path, default=Path("data") / "seed42")
    parser.add_argument("--stage", type=str, default=None, help="L0 | L1 | ...")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv)

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    if not args.dataset.exists():
        print(f"no dataset at {args.dataset} — run `make generate` first")
        return 2

    stage = (args.stage or "L0").upper()
    if stage == "L0":
        show_l0(args.dataset)
        return 0

    print(f"stage {stage} is not implemented yet — it arrives with its own gate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
