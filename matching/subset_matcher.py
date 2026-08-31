"""L3 — N:1 Candidate Generation. Guide §4.3.

For each bank credit L1 could not bridge, find which combination of open ledger
rows explains it. This is the layer that solves the actual problem of §1.4:
a credit, a pile of orders, and no join key.

    target = fee_model.expected_gross(credit.amount)   # net -> gross
    window = business days back from the credit's value date
    pool   = open orders in that window
             + open refunds in a WIDER one
    solve  = subset_sum(signed amounts, target, tolerance, max_solutions=5)

**Refunds need no special case.** `Record.signed_amount` is negative for
outflows, so a refund enters the same pool and the same solver as a sale, and
`2000 + 3500 + 2500 − 1200 = 6800` falls out of the identical code path. There is
no `if is_refund:` anywhere below — the solver never learns what a refund is.

**The windows are asymmetric, and that is the whole trick.** A cross-period
refund can be weeks old, so it would never appear in a T+2 window. Orders get
the tight window; refunds get a ~45-day lookback. That asymmetry is what makes
CROSS_PERIOD_REFUND solvable at all (§4.3b). Choosing a window by direction is
not the same as branching on refund-ness: the arithmetic stays uniform, only the
*eligibility* differs.

**Domain knowledge is a performance feature.** Subset-sum over 30 candidates is
2³⁰ ≈ 1.07 billion combinations. The T+2 business rule prunes those 30 to about
5, which is 32 — microseconds. Teams that brute-force hang at 500 records (§2.4).
"""

from __future__ import annotations

from datetime import timedelta

from core.adjudication import CandidateLeg, UnexplainedEvidence
from core.dates import DateWindow
from core.models import Direction, MatchProposal, Record
from core.reason_codes import ReasonCode
from matching.fee_model import FeeModel
from matching.protocols import MatchContext
from matching.subset_solver import SolveResult, solve

NAME = "L3_subset"

#: Confidence for a single exact solution inside the tight window (§4.3).
CONF_EXACT = 0.92
#: A single solution that needed the rounding tolerance.
CONF_TOLERANT = 0.85
#: A single solution that needed the window widened.
CONF_WIDENED = 0.78

#: Extra business days when the tight window finds nothing.
WIDEN_BY_DAYS = 3


class SubsetMatcher:
    """The N:1 layer. Allowed to be uncertain, and says so."""

    name = NAME

    def propose(self, ctx: MatchContext) -> list[MatchProposal]:
        fee = ctx.fee_model
        if fee is None:
            return []

        proposals: list[MatchProposal] = []
        for credit in ctx.open_bank_credits():
            if credit.direction is not Direction.INFLOW:
                continue
            proposal = self._explain(ctx, credit, fee)
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    # ------------------------------------------------------------------

    def _explain(
        self, ctx: MatchContext, credit: Record, fee: FeeModel
    ) -> MatchProposal | None:
        target = fee.expected_gross(credit.amount.paise)
        tolerance = fee.tolerance_paise(ctx.settings.rounding_tolerance_paise)

        # Three passes, cheapest and most certain first. Each later pass buys
        # coverage with confidence, which is exactly the trade §2.5 wants made
        # explicitly rather than hidden inside one loose threshold.
        attempts = (
            (self._pool(ctx, credit, widen=0), 0, CONF_EXACT),
            (self._pool(ctx, credit, widen=0), tolerance, CONF_TOLERANT),
            (self._pool(ctx, credit, widen=WIDEN_BY_DAYS), tolerance, CONF_WIDENED),
        )

        last: SolveResult | None = None
        last_pool: list[Record] = []
        for pool, tol, confidence in attempts:
            if not pool:
                continue
            last_pool = pool
            result = solve(
                _gross_equivalents(pool, fee),
                target,
                tol=tol,
                max_solutions=ctx.settings.max_candidates,
                max_nodes=ctx.settings.solver_node_budget,
            )
            last = result

            if result.is_ambiguous:
                self._flag_ambiguous(ctx, credit, pool, result, target, tol, fee)
                return None

            if result.solutions:
                rows = [pool[i] for i in result.solutions[0]]
                return self._propose(credit, rows, confidence, fee, target, tol)

        self._flag_unexplained(ctx, credit, target, last, last_pool, fee)
        return None

    # ------------------------------------------------------------------

    def _pool(self, ctx: MatchContext, credit: Record, *, widen: int) -> list[Record]:
        """Ledger rows that could plausibly belong to this credit.

        Two windows, because two different things are being modelled: an order
        settles on a T+2 clock, while a refund is deducted whenever it clears,
        which may be a month after the sale it reverses.

        The order window is centred on the capture date *back-solved* from the
        credit, not on a span reaching back from it. Those are very different
        sizes: a five-business-day span holds ~40 orders at this volume, while
        the back-solved date holds one day's worth. And it is exact —
        `add_business_days` and `subtract_business_days` are inverses, so a
        settlement pushed off a Sunday lands back on its true capture date
        rather than a day early. That is the whole of §2.4: the business rule,
        not the algorithm, is what turns 40 candidates into 8.
        """
        settings = ctx.settings
        expected_capture = ctx.calendar.subtract_business_days(
            credit.value_date, settings.settlement_days
        )
        if widen:
            order_window = DateWindow(
                ctx.calendar.subtract_business_days(expected_capture, widen),
                ctx.calendar.add_business_days(expected_capture, widen),
            )
        else:
            order_window = DateWindow(expected_capture, expected_capture)
        refund_window = DateWindow(
            credit.value_date - timedelta(days=settings.refund_lookback_days),
            credit.value_date,
        )
        windows = {
            Direction.INFLOW: order_window,
            Direction.OUTFLOW: refund_window,
        }
        return [
            row
            for row in ctx.open_ledger_rows()
            if windows[row.direction].contains(row.value_date)
        ]

    def _propose(
        self,
        credit: Record,
        rows: list[Record],
        confidence: float,
        fee: FeeModel,
        target: int,
        tol: int,
    ) -> MatchProposal:
        # Gross-EQUIVALENT, not face value. The solver matched these rows
        # against `target` through `_gross_equivalents`, and `tol` is the
        # tolerance it applied there; measuring the drift in face-value space
        # and printing it beside that tolerance compares two different
        # quantities. On a settlement carrying a refund the two diverge by the
        # refund's own MDR and GST, so the line claimed "within 717 paise of
        # rounding tolerance 0" about a match that reconstructed exactly.
        gross = sum(_gross_equivalents(rows, fee))
        drift = gross - target
        orders = [r for r in rows if r.direction is Direction.INFLOW]
        refunds = [r for r in rows if r.direction is Direction.OUTFLOW]

        reason = (
            f"{len(orders)} order(s) totalling {_rupees(sum(r.signed_amount for r in orders))}"
        )
        if refunds:
            reason += (
                f" less {len(refunds)} refund(s) of "
                f"{_rupees(-sum(r.signed_amount for r in refunds))}"
            )
        reason += (
            f" invert to the {credit.amount} credit at the inferred "
            f"{fee.rate:.3%} MDR"
        )
        if drift:
            reason += f" (within {abs(drift)} paise of rounding tolerance {tol})"

        evidence = ["amount", "capture_date", "inferred_fee_rate"]
        if refunds:
            evidence.append("refund_lookback")

        return MatchProposal(
            bank_utr=credit.external_id,
            ledger_ids=frozenset(r.external_id for r in rows),
            confidence=_capped(confidence, fee),
            strategy=NAME,
            evidence=tuple(evidence),
            reason=reason,
        )

    # ------------------------------------------------------------------

    def _flag_ambiguous(
        self,
        ctx: MatchContext,
        credit: Record,
        pool: list[Record],
        result: SolveResult,
        target: int,
        tol: int,
        fee: FeeModel,
    ) -> None:
        """More than one combination explains this credit.

        Recorded as an ambiguity for L4 to adjudicate (§4.4 job A). Until L4
        exists — and permanently under `--no-llm` — it falls through to an
        exception rather than picking one, because guessing between two
        arithmetically identical answers is how a wrong journal entry gets
        posted (§4.4).

        Flagged AMBIGUOUS_UNADJUDICATED, never ADJUDICATION_REJECTED: nothing
        was adjudicated here, so nothing was rejected. The distinction keeps
        the exception page truthful on a --no-llm run and keeps "the model
        was wrong" separate from "the model was never asked".
        """
        options = [
            sorted(pool[i].external_id for i in solution)
            for solution in result.solutions
        ]
        # The pool as L3 valued it, so L4's guardrail can re-add the chosen
        # combination without ever seeing the fee model (§4.4, §3.2).
        gross_of = dict(
            zip(
                (row.external_id for row in pool),
                _gross_equivalents(pool, fee),
                strict=True,
            )
        )
        ctx.record_ambiguity(
            credit,
            options,
            target,
            gross_of=gross_of,
            tolerance_paise=tol,
            fee_rate=fee.rate if fee.is_usable else None,
            # False when the solver stopped at its cap, which means the true
            # combination may not be on the list at all. On seed 42 it is not:
            # the correct set reconstructs 53 paise below target and the
            # tolerance is 50, so no number of candidates would have contained
            # it. L4 is told, and may answer NONE (§4.4).
            exhaustive=result.exhausted,
        )
        ctx.flag(
            ReasonCode.AMBIGUOUS_UNADJUDICATED,
            ref=credit.external_id,
            what=(
                f"{len(options)} different combinations of ledger rows each "
                f"explain {credit.external_id}, the {credit.amount} credit of "
                f"{credit.value_date}, exactly."
            ),
            why=(
                "Arithmetic cannot separate them — they sum to the same "
                "figure. Choosing needs the narration, the settlement batch "
                "or the capture timing, and no adjudicator was asked."
            ),
            amount_paise=credit.amount.paise,
            raised_by=NAME,
            value_date=credit.value_date,
        )

    def _flag_unexplained(
        self,
        ctx: MatchContext,
        credit: Record,
        target: int,
        result: SolveResult | None,
        pool: list[Record],
        fee: FeeModel,
    ) -> None:
        incomplete = result is not None and not result.exhausted
        # Handed to L4 job B, which can say WHY in a sentence a controller can
        # act on. Recorded whether or not an adjudicator exists: on --no-llm it
        # simply goes unused, and the deterministic `why` below stands.
        ctx.record_unexplained(
            UnexplainedEvidence(
                ref=credit.external_id,
                amount_paise=credit.amount.paise,
                narration=credit.narration,
                expected_gross_paise=target,
                nearest_rows=_nearest(pool, target, fee),
                open_pool_rows=len(pool),
                open_pool_paise=sum(_gross_equivalents(pool, fee)),
                value_date=credit.value_date,
                inferred_fee_rate=fee.rate if fee.is_usable else None,
            )
        )
        ctx.flag(
            ReasonCode.AMOUNT_MISMATCH,
            ref=credit.external_id,
            what=(
                f"No combination of open ledger rows explains "
                f"{credit.external_id}, the {credit.amount} credit of "
                f"{credit.value_date}."
            ),
            why=(
                "The search was cut short before it could finish, so this is "
                "'not found yet' rather than 'not there'."
                if incomplete
                else (
                    f"Inverting the credit at the inferred fee rate expects "
                    f"{_rupees(target)} of gross, and no subset of the orders "
                    "captured in the settlement window reaches it. Consistent "
                    "with a partial refund or a different MDR slab."
                )
            ),
            amount_paise=credit.amount.paise,
            raised_by=NAME,
            value_date=credit.value_date,
        )


def _gross_equivalents(rows: list[Record], fee: FeeModel) -> list[int]:
    """Every row as the gross it represents, so one sum can compare to one target.

    An order's gross-equivalent is simply its own amount — it *is* gross. A
    refund's is not its face value: refunds are deducted from the payout AFTER
    the MDR and GST are taken, so a ₹240 refund reduces a gross-space total by
    ₹245.30 at a 1.83% rate, not by ₹240.

    Mixing the two spaces is a quiet, plausible-looking failure. Every
    cross-period refund missed by about 2% of the refund — 529 paise on the
    settlement above — which is far too small to look like a wrong answer and
    far too large to fall inside the rounding tolerance. It simply produced no
    solution, and the credit became an unexplained exception.

    This is a per-row transform of the same question — "what gross does this row
    stand for?" — not a branch on refund-ness. The solver still receives plain
    signed integers and never learns that refunds exist (§4.3a).
    """
    return [
        row.signed_amount
        if row.direction is Direction.INFLOW
        else -fee.expected_gross(-row.signed_amount)
        for row in rows
    ]


def _capped(confidence: float, fee: FeeModel) -> float:
    """Never claim certainty on top of a rate that was guessed.

    A LOW_CONFIDENCE fee model means the MDR was never inferred, so `target` is
    derived from a fallback constant rather than from the merchant's data. Every
    amount built on it is a guess, and a guess does not belong in the auto-post
    band (§2.5, §4.2 step 5).
    """
    if fee.is_usable:
        return confidence
    return min(confidence, 0.60)


def _rupees(paise: int) -> str:
    from core.money import Money

    return str(Money(paise))


#: How many near-misses job B is shown. Enough to reason from, few enough that
#: a batch of exceptions stays a small request.
NEAREST_ROWS = 6


def _nearest(rows: list[Record], target: int, fee: FeeModel) -> tuple[CandidateLeg, ...]:
    """The open rows closest to the target, as gross-equivalents.

    Ranked by distance from the target so the model sees the near-miss it is
    being asked about rather than an arbitrary slice of the window. Ties break
    on the order id, because a pool that reorders between runs would change the
    prompt, change the cache key, and quietly cost a request per run.
    """
    valued = list(zip((r.external_id for r in rows), _gross_equivalents(rows, fee), strict=True))
    by_id = {r.external_id: r for r in rows}
    ranked = sorted(valued, key=lambda pair: (abs(pair[1] - target), pair[0]))
    return tuple(
        CandidateLeg(
            order_id=order_id,
            gross_equivalent_paise=gross,
            capture_date=by_id[order_id].value_date,
        )
        for order_id, gross in ranked[:NEAREST_ROWS]
    )
