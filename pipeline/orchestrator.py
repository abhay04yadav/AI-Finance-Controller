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

from adjudication.null_adjudicator import NullAdjudicator
from adjudication.protocols import Adjudicator
from core.config import Settings
from core.dates import BusinessCalendar
from core.run_result import ExceptionOutcome, MatchOutcome, RunResult
from exceptions_.actions import available_for
from ingest.normalizer import load_dataset
from matching.protocols import MatchContext, MatchStrategy
from persistence.repositories import JournalRepository
from pipeline.adjudication_step import adjudicate, apply_hypotheses
from pipeline.posting_step import Poster
from pipeline.trace import build_ambiguous, build_unexplained


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
        adjudicator: Adjudicator | None = None,
        repository: JournalRepository | None = None,
    ) -> None:
        self._strategies = tuple(strategies)
        self._calendar = calendar
        self._settings = settings
        self._fee_model_enabled = fee_model_enabled
        # Null Object, not None: the orchestrator never asks whether it has an
        # adjudicator, so there is no `if` here for a later gate to get wrong.
        self._adjudicator: Adjudicator = adjudicator or NullAdjudicator()
        # Injected so the API can keep the book after the run returns and go on
        # issuing journal numbers as a controller acts. `None` means the Poster
        # opens a fresh one, which is what the eval and every test want.
        self._repository = repository

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

        # L4 — only what arithmetic could not settle (§4.4). Runs before L5 so
        # a verdict's rows are claimed before anything is posted.
        t = time.perf_counter()
        adjudication = adjudicate(
            ctx,
            self._adjudicator,
            records=len(ingested.records),
            settings=self._settings,
        )
        timings[self._adjudicator.name] = (time.perf_counter() - t) * 1000

        # L5 — the step that closes the loop (§4.5).
        t = time.perf_counter()
        posting = Poster(
            self._repository, settings=self._settings
        ).post_all(ctx)
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

        # One credit, one card (§8.2). Several checks can fire on the same
        # reference and each be true; the most explanatory one is shown and the
        # rest are folded into it. See `core.reason_codes._PRECEDENCE`.
        exceptions = _one_card_per_ref(exceptions)

        # ACTION is the third leg of every card (§8.2). Attached here, from the
        # registry, so the UI renders whatever is_available() returned rather
        # than a hardcoded button list (§8.3).
        exceptions = [
            replace(e, actions=available_for(e)) if not e.actions else e
            for e in exceptions
        ]

        # Job B's hypotheses become the WHY on the cards they explain (§8.2).
        final = apply_hypotheses(tuple(exceptions), adjudication)

        # The books count what the exception page shows. Recomputed from the
        # deduped list rather than from the raw flags, because two screens
        # quoting different totals for the same money is the defect this whole
        # collapse exists to fix — fixing it on one screen only would be worse
        # than not fixing it at all.
        position = posting.cash_position
        problems = [e for e in final if not e.is_in_transit]
        position = replace(
            position,
            exceptions=len(problems),
            exceptions_paise=sum(e.amount_paise or 0 for e in problems),
        )

        # The §8.5 trail: matches got theirs while their entries were built;
        # exceptions get theirs from the evidence L3 recorded when it gave up.
        # Merged rather than rebuilt, because the residual pool L3 searched no
        # longer exists by the time the run returns.
        by_utr = {a.credit_utr: a for a in ctx.ambiguities}
        traces = dict(posting.traces)
        for evidence in ctx.unexplained:
            traces[evidence.ref] = build_unexplained(
                evidence, by_utr.get(evidence.ref)
            )
        # An ambiguous credit has no unexplained evidence — the arithmetic
        # worked, several times over — so its trail is drawn from the candidates
        # instead. Without this the one card that most needs a diagram is the
        # only card without one.
        for ambiguity in ctx.ambiguities:
            traces.setdefault(ambiguity.credit_utr, build_ambiguous(ambiguity))

        return RunResult(
            matches=matches,
            exceptions=final,
            records_processed=len(ingested.records),
            # Records that REACHED L4, cached or not — the numerator in §2.2's
            # under-10% budget. Cost counts only requests actually made.
            llm_calls=adjudication.calls,
            llm_api_requests=adjudication.api_requests,
            llm_cost_paise=adjudication.cost_paise,
            adjudication_notes=adjudication.notes,
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
            cash_position=position,
            duplicate_postings_refused=posting.duplicates_refused,
            traces=traces,
            entry_numbers=posting.entry_numbers,
        )


def _one_card_per_ref(
    exceptions: list[ExceptionOutcome],
) -> list[ExceptionOutcome]:
    """Collapse several findings about one credit into the card that explains it.

    A settlement whose orders are absent from the ledger trips two checks: the
    coverage check says MISSING_IN_LEDGER, and L3 says AMOUNT_MISMATCH because
    it could not explain the credit — which is true, and is true *because* the
    orders are missing. Two cards for one problem overstates the count, and
    because the header sums the cards, overstates the money too.

    The surviving card keeps the more explanatory reason code and gains a line
    naming what else was raised, so nothing is silently dropped. In-transit rows
    never take part: they live in their own collection and share no ref space
    with bank credits (Appendix A).
    """
    from core.reason_codes import precedence_of

    best: dict[str, ExceptionOutcome] = {}
    folded: dict[str, list[ExceptionOutcome]] = {}
    order: list[str] = []

    for exc in exceptions:
        # In-transit rows are keyed apart so a ledger row and a bank credit
        # that happen to share a reference cannot collide.
        key = f"in-transit:{exc.ref}" if exc.is_in_transit else exc.ref
        if key not in best:
            best[key] = exc
            folded[key] = []
            order.append(key)
            continue
        incumbent = best[key]
        if precedence_of(exc.reason_code) < precedence_of(incumbent.reason_code):
            best[key] = exc
            folded[key].append(incumbent)
        else:
            folded[key].append(exc)

    out: list[ExceptionOutcome] = []
    for key in order:
        exc = best[key]
        others = folded[key]
        if others:
            also = ", ".join(sorted({str(o.reason_code) for o in others}))
            exc = replace(
                exc,
                why=(
                    f"{exc.why} Also raised as {also}, which follows from this "
                    "and is not a separate problem."
                ).strip(),
            )
        out.append(exc)
    return out
