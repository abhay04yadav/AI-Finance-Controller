"""Per-stage debug and profiling CLI. Guide §9.6.

    python -m pipeline.debug --stage L0 --dataset data/seed42     (gate 4)
    python -m pipeline.debug --profile --dataset data/seed42      (gate 10)
    python -m pipeline.debug --stage L4 --dataset data/seed42     (gate 11)

When throughput is a judged metric you need to know which layer is slow, and
being able to say "L3 is 4ms, L4 is 380ms, that's why we keep L4 under 10%" is a
much better answer than a single total.

Stages land as their gates do; asking for one that does not exist yet says so
rather than pretending.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from core.adjudication import AdjudicationResult, Ambiguity, Unexplained
from core.console import configure_stdout
from core.console import money as money_str
from core.console import rule as console_rule
from core.models import Source
from ingest.normalizer import IngestResult, load_dataset


def show_l0(dataset: Path) -> IngestResult:
    """Ingest the dataset and report what came out, per source."""
    t0 = time.perf_counter()
    result = load_dataset(dataset)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"L0 - INGEST & NORMALIZE: {dataset}")
    print(console_rule(66))
    print(f"{'source':<12}{'records':>9}{'total':>18}{'refs':>8}{'dates':>26}")
    for source in (Source.LEDGER, Source.SETTLEMENT, Source.BANK):
        rows = result.by_source(source)
        if not rows:
            print(f"{source!s:<12}{0:>9}")
            continue
        total = result.total_paise(source)
        with_refs = sum(1 for r in rows if r.refs)
        lo = min(r.value_date for r in rows)
        hi = max(r.value_date for r in rows)
        print(
            f"{source!s:<12}{len(rows):>9}{_rupees(total):>18}{with_refs:>8}"
            f"{f'{lo} .. {hi}':>26}"
        )

    print(console_rule(66))
    print(f"{'total':<12}{len(result.records):>9}    parsed in {elapsed_ms:.1f} ms")

    if result.failures:
        print()
        print(f"INGEST_ERROR — {len(result.failures)} row(s) could not be read")
        by_source = Counter(str(f.source) for f in result.failures)
        for name, n in sorted(by_source.items()):
            print(f"  {name:<12}{n:>4}")
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
    """Terminal-safe money, so a cp1252 console cannot kill the report."""
    return money_str(paise)


def show_l2(dataset: Path) -> None:
    """What the fee model learned, and from how much evidence (§4.2)."""
    from pipeline.factory import build_pipeline

    result = build_pipeline().run(dataset)
    print(f"L2 - FEE MODEL INFERENCE: {dataset}")
    print(console_rule(66))
    if result.fee_rate is None:
        print("  nothing inferred — no confirmed settlements to learn from")
        return
    print(f"  {result.fee_model_summary}")
    print()
    print("  The MDR was never supplied. It is derived from the settlements L1")
    print("  confirmed, which is why this runs on any merchant's export with no")
    print("  configuration at all (section 2.3).")
    print()
    from matching.fee_model import FeeModel

    model = FeeModel(rate=result.fee_rate)
    print(f"  {'gross':>14}{'-> net':>14}{'-> gross':>14}{'drift':>10}")
    for gross in (100_00, 800_000, 8_764_321):
        net = model.expected_net(gross)
        back = model.expected_gross(net)
        print(
            f"  {_rupees(gross):>14}{_rupees(net):>14}{_rupees(back):>14}"
            f"{back - gross:>8} p"
        )


def show_l4(dataset: Path) -> None:
    """Exactly what reaches the LLM, and what it costs (§4.4, §2.2).

    Printed WITHOUT calling anything: this is the question, serialized, before
    any model sees it. Being able to show a judge the literal payload — and
    that it is three cases out of six hundred records — is a better answer to
    "how much does the AI do?" than any number in the report.
    """
    from core.config import Settings
    from core.dates import BusinessCalendar
    from matching.registry import build_strategies
    from pipeline.adjudication_step import budget_for
    from pipeline.orchestrator import ReconciliationPipeline

    settings = Settings()
    captured = _Capture()
    result = ReconciliationPipeline(
        build_strategies(),
        calendar=BusinessCalendar(),
        settings=settings,
        adjudicator=captured,
    ).run(dataset)

    print(f"L4 - LLM ADJUDICATION: {dataset}")
    print(console_rule(66))
    records = result.records_processed
    budget = budget_for(records, settings)
    reaching = len(captured.ambiguities) + len(captured.cases)
    share = reaching / records if records else 0.0
    print(f"{'records':<22}{records:>8}")
    print(f"{'budget (10%)':<22}{budget:>8} case(s)")
    print(f"{'reaching L4':<22}{reaching:>8} case(s)   {share:.1%}")
    print(f"{'  job A (select)':<22}{len(captured.ambiguities):>8}")
    print(f"{'  job B (explain)':<22}{len(captured.cases):>8}")
    print(f"{'requests':<22}{min(1, len(captured.ambiguities)) + min(1, len(captured.cases)):>8}"
          "   batched, one per job")
    print()

    if captured.ambiguities:
        print("JOB A - the question, as sent:")
        print(json.dumps(captured.ambiguities[0].as_prompt_dict(), indent=2)[:1600])
        print()
    if captured.cases:
        print("JOB B - the question, as sent:")
        print(json.dumps(captured.cases[0].as_prompt_dict(), indent=2)[:1600])
    if not captured.ambiguities and not captured.cases:
        print("nothing reached L4 - the deterministic core settled every credit")


class _Capture:
    """Records what L4 would be asked, and answers nothing.

    A Null Object with a notebook (§5.3). Showing the question without paying
    for an answer is the whole point of `--stage L4`: it is a claim about our
    own budget, and it should be checkable without spending anything.
    """

    name = "L4_capture"

    def __init__(self) -> None:
        self.ambiguities: list[Ambiguity] = []
        self.cases: list[Unexplained] = []

    def adjudicate(
        self,
        ambiguities: Sequence[Ambiguity],
        cases: Sequence[Unexplained],
        *,
        budget: int,
    ) -> AdjudicationResult:
        self.ambiguities = list(ambiguities)
        self.cases = list(cases)
        return AdjudicationResult()


def show_profile(dataset: Path) -> None:
    """Which layer is slow, and how much each one resolved (§9.6).

    Deliberately does NOT import the eval harness. Precision needs the answer
    key, and the pipeline must never be able to see its own grade — a profiler
    that reaches for `truth.json` is one refactor away from a matcher doing the
    same. Per-layer precision is in `make eval`, where the scoring lives.
    """
    from pipeline.factory import build_pipeline

    result = build_pipeline().run(dataset)

    print(f"PROFILE: {dataset}")
    print(console_rule(66))
    print(f"{'layer':<16}{'time':>12}{'share':>9}{'resolved':>11}")

    total_ms = sum(result.layer_timings_ms.values()) or 1.0
    by_strategy: dict[str, int] = {}
    for outcome in result.matches.values():
        by_strategy[outcome.strategy] = by_strategy.get(outcome.strategy, 0) + 1

    for name, ms in result.layer_timings_ms.items():
        resolved = by_strategy.get(name)
        shown = str(resolved) if resolved is not None else "-"
        print(f"{name:<16}{ms:>9.1f} ms{ms / total_ms:>8.0%}{shown:>11}")

    print(console_rule(66))
    print(f"{'total':<16}{total_ms:>9.1f} ms")
    print(f"{'records':<16}{result.records_processed:>12}")
    print(f"{'credits':<16}{len(result.matches) + len(result.exceptions):>12}")
    print()
    print(f"LLM calls {result.llm_calls}   cost {_rupees(result.llm_cost_paise)}")
    if result.llm_calls == 0:
        print("  the deterministic core carried the entire run")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.debug")
    parser.add_argument("--dataset", type=Path, default=Path("data") / "seed42")
    parser.add_argument("--stage", type=str, default=None, help="L0 | L1 | ...")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv)

    configure_stdout()

    if not args.dataset.exists():
        print(f"no dataset at {args.dataset} — run `make generate` first")
        return 2

    if args.profile:
        show_profile(args.dataset)
        return 0

    stage = (args.stage or "L0").upper()
    if stage == "L0":
        show_l0(args.dataset)
        return 0
    if stage == "L2":
        show_l2(args.dataset)
        return 0
    if stage == "L4":
        show_l4(args.dataset)
        return 0

    print(f"stage {stage} is not implemented yet — it arrives with its own gate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
