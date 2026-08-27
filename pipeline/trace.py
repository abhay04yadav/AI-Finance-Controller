"""Building the reconciliation trace from a run. Guide §8.5.

The value objects live in `core/trace.py` — `core/` may import nothing from the
project, and assembling a trace needs records, a fee model and a match. This is
the half that knows what a settlement window is.

Two shapes come out of here, because §8.5's trail has two endings:

* **explained** — a match. The orders that were claimed, the settlement they
  were reported in, the fee and GST that were subtracted, and the credit that
  landed. The steps reconstruct the credit exactly.
* **unexplained** — an exception. The nearest open row L3 could see, the
  arithmetic it tried, and the gap it could not close, which the UI draws in red.

The rejected candidate is carried too. A trace that shows only the path taken is
a diagram; one that shows the road not taken, and why, is an argument a
controller can disagree with.
"""

from __future__ import annotations

from core.adjudication import Ambiguity, UnexplainedEvidence
from core.models import Direction, MatchProposal, Record
from core.trace import Trace, TraceNode, TraceStep
from matching.fee_model import FeeModel

#: How many source rows the trail draws before collapsing the rest. Beyond this
#: the SVG stops being readable, and an unreadable diagram is worse than a count.
MAX_NODES = 4


def build_explained(
    proposal: MatchProposal,
    credit: Record,
    rows: list[Record],
    fee: FeeModel,
    *,
    fee_paise: int,
    gst_paise: int,
) -> Trace:
    """The trail behind a match: what was claimed, what was deducted, what landed."""
    orders = [r for r in rows if r.direction is Direction.INFLOW]
    refunds = [r for r in rows if r.direction is Direction.OUTFLOW]
    gross = sum(r.amount.paise for r in orders)
    refunded = sum(r.amount.paise for r in refunds)

    steps: list[TraceStep] = [
        TraceStep(
            label=f"{len(orders)} order(s) gross",
            signed_paise=gross,
            kind="open",
        )
    ]
    if refunded:
        steps.append(
            TraceStep(
                label="refunds",
                signed_paise=-refunded,
                note=f"{len(refunds)} row(s), deducted from the payout",
            )
        )
    if fee_paise:
        note = (
            f"fee @ {fee.rate:.2%}"
            if proposal.settlement_id is None
            else "fee as the gateway stated it"
        )
        steps.append(TraceStep(label="gateway fee", signed_paise=-fee_paise, note=note))
    if gst_paise:
        steps.append(
            TraceStep(
                label="GST on fee",
                signed_paise=-gst_paise,
                note=f"{fee.gst_rate:.0%} of the fee, reclaimable",
            )
        )

    reconstructed = gross - refunded - fee_paise - gst_paise
    steps.append(
        TraceStep(label="expected in bank", signed_paise=reconstructed, kind="subtotal")
    )
    residual = reconstructed - credit.amount.paise
    if residual:
        steps.append(
            TraceStep(
                label="rounding",
                signed_paise=-residual,
                note="per-order gateway rounding, written off",
                kind="residual",
            )
        )

    return Trace(
        ref=proposal.bank_utr,
        outcome="explained",
        nodes=_nodes(rows),
        steps=tuple(steps),
        settlement_id=proposal.settlement_id,
        settlement_known=proposal.settlement_id is not None,
        credit_paise=credit.amount.paise,
        credit_value_date=credit.value_date,
        residual_paise=0,
        fee_rate=fee.rate if fee.is_usable else None,
        gst_rate=fee.gst_rate,
    )


def build_unexplained(
    evidence: UnexplainedEvidence, ambiguity: Ambiguity | None = None
) -> Trace:
    """The trail behind an exception: how close L3 got, and what is missing.

    The arithmetic runs in the direction the matcher ran it — invert the credit
    to the gross it implies, then look for rows that reach it — because that is
    what the controller is being asked to disagree with.
    """
    nearest = evidence.nearest_rows[:MAX_NODES]
    nodes = tuple(
        TraceNode(
            id=leg.order_id,
            amount_paise=leg.gross_equivalent_paise,
            value_date=leg.capture_date,
            kind="refund" if leg.gross_equivalent_paise < 0 else "order",
            settlement_id=leg.settlement_id,
            rejected=index > 0,
            rejected_because=(
                "further from the expected gross than the nearest row"
                if index > 0
                else ""
            ),
        )
        for index, leg in enumerate(nearest)
    )

    steps: list[TraceStep] = [
        TraceStep(
            label="credit received",
            signed_paise=evidence.amount_paise,
            kind="open",
        ),
        TraceStep(
            label="expected gross",
            signed_paise=evidence.expected_gross_paise,
            note=(
                f"credit inverted at the inferred {evidence.inferred_fee_rate:.2%} MDR"
                if evidence.inferred_fee_rate
                else "credit inverted at the fallback MDR"
            ),
            kind="subtotal",
        ),
    ]
    if nearest:
        best = nearest[0]
        steps.append(
            TraceStep(
                label=f"nearest open row {best.order_id}",
                signed_paise=best.gross_equivalent_paise,
                note=f"captured {best.capture_date.isoformat()}",
            )
        )
        residual = evidence.expected_gross_paise - best.gross_equivalent_paise
    else:
        residual = evidence.expected_gross_paise

    steps.append(
        TraceStep(
            label="unexplained",
            signed_paise=-abs(residual),
            note="no combination of open rows closes this",
            kind="residual",
        )
    )

    return Trace(
        ref=evidence.ref,
        outcome="unexplained",
        nodes=nodes,
        steps=tuple(steps),
        settlement_id=None,
        settlement_known=False,
        credit_paise=evidence.amount_paise,
        credit_value_date=evidence.value_date,
        residual_paise=abs(residual),
        fee_rate=evidence.inferred_fee_rate,
        gst_rate=None,
        open_pool_rows=evidence.open_pool_rows,
        open_pool_paise=evidence.open_pool_paise,
        candidates=ambiguity.options if ambiguity else (),
    )


def build_ambiguous(ambiguity: Ambiguity) -> Trace:
    """The trail behind a credit several combinations explain equally well.

    Different from an unexplained trace in what it is arguing. There, the point
    is a gap nothing closes; here the arithmetic closes several times over, and
    the drawing has to show *why that is the problem* — the chosen combination's
    rows beside the ones that fit just as well.

    The first candidate's rows are drawn as the path, the rest as roads not
    taken. Neither is a recommendation: nothing was matched, and the card says
    so. It is the shape of the ambiguity that a controller needs to see.
    """
    first, *rest = ambiguity.candidates
    nodes = [
        TraceNode(
            id=leg.order_id,
            amount_paise=leg.gross_equivalent_paise,
            value_date=leg.capture_date,
            kind="refund" if leg.gross_equivalent_paise < 0 else "order",
            settlement_id=leg.settlement_id,
        )
        for leg in first.legs[:MAX_NODES]
    ]
    # One row from a rival combination, to show what else fits. It has to be a
    # row the winning combination does NOT contain: drawing the same order twice
    # — once as taken, once as rejected — makes the diagram look like a bug.
    shown = {leg.order_id for leg in first.legs[:MAX_NODES]}
    for candidate in rest:
        distinct = next((x for x in candidate.legs if x.order_id not in shown), None)
        if distinct is None:
            continue
        nodes.append(
            TraceNode(
                id=distinct.order_id,
                amount_paise=distinct.gross_equivalent_paise,
                value_date=distinct.capture_date,
                kind="refund" if distinct.gross_equivalent_paise < 0 else "order",
                settlement_id=distinct.settlement_id,
                rejected=True,
                rejected_because=f"in candidate {candidate.id}, which fits too",
            )
        )
        break

    steps = (
        TraceStep(
            label="credit received",
            signed_paise=ambiguity.credit_paise,
            kind="open",
        ),
        TraceStep(
            label="expected gross",
            signed_paise=ambiguity.target_paise,
            note=(
                f"credit inverted at the inferred {ambiguity.inferred_fee_rate:.2%} MDR"
                if ambiguity.inferred_fee_rate
                else "credit inverted at the fallback MDR"
            ),
            kind="subtotal",
        ),
        TraceStep(
            label=f"{len(ambiguity.candidates)} combinations reach it",
            signed_paise=first.gross_paise,
            note=(
                f"within the {ambiguity.tolerance_paise} paise rounding tolerance"
                if ambiguity.tolerance_paise
                else "exactly"
            ),
        ),
        TraceStep(
            label="unresolved",
            signed_paise=0,
            note="no evidence separates them, so nothing was matched",
            kind="residual",
        ),
    )

    return Trace(
        ref=ambiguity.credit_utr,
        outcome="unexplained",
        nodes=tuple(nodes),
        steps=steps,
        settlement_id=None,
        settlement_known=False,
        credit_paise=ambiguity.credit_paise,
        credit_value_date=ambiguity.credit_value_date,
        residual_paise=0,
        fee_rate=ambiguity.inferred_fee_rate,
        gst_rate=None,
        candidates=ambiguity.options,
    )


def _nodes(rows: list[Record]) -> tuple[TraceNode, ...]:
    ordered = sorted(rows, key=lambda r: (-abs(r.signed_amount), r.external_id))
    return tuple(
        TraceNode(
            id=row.external_id,
            amount_paise=row.signed_amount,
            value_date=row.value_date,
            kind="order" if row.direction is Direction.INFLOW else "refund",
        )
        for row in ordered[:MAX_NODES]
    )
