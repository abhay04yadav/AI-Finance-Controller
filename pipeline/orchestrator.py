"""ReconciliationPipeline — Chain of Responsibility. Guide §5.4.

Owns WIRING, not business logic. Each layer consumes the RESIDUAL of the
previous one; nothing L1 resolved is ever reconsidered by L3. That is what keeps
the LLM budget under 10%.

This file contains NO fee arithmetic, NO date math, NO SQL and NO prompt text.
If business logic appears here during the build, it belongs somewhere else.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from core.config import Settings
from core.dates import BusinessCalendar
from core.run_result import ExceptionOutcome, MatchOutcome, RunResult
from exceptions_.actions import available_for
from ingest.normalizer import load_dataset
from matching.protocols import MatchContext, MatchStrategy
from pipeline.posting_step import Poster


class ReconciliationPipeline:
    """Runs the layers in order and reports what came out."""

    name = "pipeline"

    def __init__(
        self,
        strategies: Sequence[MatchStrategy],
        *,
        calendar: BusinessCalendar,
        settings: Settings,
        fee_model_enabled: bool = True,
    ) -> None:
        self._strategies = tuple(strategies)
        self._calendar = calendar
        self._settings = settings
        self._fee_model_enabled = fee_model_enabled

    def run(self, dataset: Path) -> RunResult:
        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        ingested = load_dataset(dataset)
        timings["L0_ingest"] = (time.perf_counter() - t0) * 1000

        ctx = MatchContext.build(
            ingested.records, calendar=self._calendar, settings=self._settings
        )
        ctx.fee_model_enabled = self._fee_model_enabled

        for strategy in self._strategies:
            t = time.perf_counter()
            for proposal in strategy.propose(ctx):
                ctx.accept(proposal)
            # e.g. the fee model, once L1 has confirmed enough pairs (§5.4)
            ctx.refresh_derived()
            timings[strategy.name] = (time.perf_counter() - t) * 1000

        # L5 — the step that closes the loop (§4.5).
        t = time.perf_counter()
        posting = Poster(settings=self._settings).post_all(ctx)
        timings["L5_post"] = (time.perf_counter() - t) * 1000

        matches = {
            p.bank_utr: MatchOutcome(
                utr=p.bank_utr,
                ledger_ids=p.ledger_ids,
                confidence=p.confidence,
                strategy=p.strategy,
                reason=p.reason,
                evidence=p.evidence,
            )
            for p in ctx.accepted
        }

        exceptions = [
            ExceptionOutcome(
                ref=f.ref,
                reason_code=f.reason_code,
                what=f.what,
                why=f.why,
                amount_paise=f.amount_paise,
                value_date=f.value_date,
            )
            for f in ctx.flags
        ]
        # Unsettled ledger rows, including in-transit money. AWAITING_SETTLEMENT
        # is reported so a controller can see it, but it is NOT counted among
        # the exceptions in the cash position (Appendix A).
        exceptions += [
            ExceptionOutcome(
                ref=f.ref,
                reason_code=f.reason_code,
                what=f.what,
                why=f.why,
                amount_paise=f.amount_paise,
                value_date=f.value_date,
            )
            for f in posting.findings
        ]
        # A row that could not even be read is an exception too (§4.0 trap).
        exceptions += [
            ExceptionOutcome(
                ref=f"{f.source}:{f.line_no}",
                reason_code=f.reason_code,
                what=f.reason,
                why="The row could not be read unambiguously and was not repaired.",
            )
            for f in ingested.failures
        ]

        # ACTION is the third leg of every card (§8.2). Attached here, from the
        # registry, so the UI renders whatever is_available() returned rather
        # than a hardcoded button list (§8.3).
        exceptions = [
            replace(e, actions=available_for(e)) if not e.actions else e
            for e in exceptions
        ]

        return RunResult(
            matches=matches,
            exceptions=tuple(exceptions),
            records_processed=len(ingested.records),
            layer_timings_ms=timings,
            # None unless something was actually learned. Reporting the
            # LOW_CONFIDENCE fallback as an inferred rate would contradict the
            # summary line beside it and overstate what the system knows.
            fee_rate=(
                ctx.fee_model.rate
                if ctx.fee_model and ctx.fee_model.is_usable
                else None
            ),
            fee_model_summary=ctx.fee_model.describe() if ctx.fee_model else "",
            entries=posting.entries,
            review_queue=posting.review_queue,
            cash_position=posting.cash_position,
            duplicate_postings_refused=posting.duplicates_refused,
        )
