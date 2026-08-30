"""The books, the tie-out and the cash position. Guide §1.6, §4.5, §8.4.

Frame 2c shows arithmetic, not a summary: four lines and a total, with a claim
that they add up. So this module returns the addends **and** the total, and a
`ties` flag computed from them — the frontend prints the tick only when the
backend says the sum holds, and never because a designer drew one.

    cash in bank (confirmed)
  + gateway fee expense
  + GST input credit claimable
  + rounding write-off
  ─────────────────────────
  = revenue recognised

Frame 3b's "how it closed" block reads from the audit trail, not from a counter:
"resolved by hand 9, of those reversed 1" is a projection over recorded events
(`AuditTrail.hand_resolution`), which stays correct after a reload and correct
with two tabs open. A number a component increments is neither.
"""

from __future__ import annotations

from typing import Any

from core.money import Money
from posting.cash_position import compute_cash_position


def current_position(run: Any) -> Any:
    """The cash position as the ledger stands NOW.

    `result.cash_position` is where the pipeline left things, before anybody
    approved or acted on anything. Approving a queued entry posts a real
    journal entry into the repository, so a books screen reading the snapshot
    reports a state that stopped being true the moment a controller did their
    job — revenue, suspense and the rounding write-off all frozen while the
    audit trail moved on without them.

    Recomputed from the repository, which holds every entry the run posted and
    every entry a person has posted since. The pending/exception counts come
    from the same decision state the other two screens are filtered by, so all
    three screens answer with one number.
    """
    snapshot = run.result.cash_position
    if snapshot is None:  # pragma: no cover - a run always posts
        return None

    review_open = [
        i
        for i in run.result.review_queue
        if run.review_decisions.get(i.utr) is None
    ]
    acted = run.trail.acted_subjects()
    exceptions_open = [
        e
        for e in run.result.exceptions
        if not e.is_in_transit and e.ref not in acted
    ]
    return compute_cash_position(
        run.repository.all(),
        in_transit=snapshot.in_transit,
        pending_review=len(review_open),
        pending_review_paise=sum(
            i.prepared_entry.amount_for("1000 Bank Account") for i in review_open
        ),
        exceptions=len(exceptions_open),
        exceptions_paise=sum(e.amount_paise or 0 for e in exceptions_open),
    )


def books_payload(run: Any) -> dict[str, Any]:
    result = run.result
    position = current_position(run)
    if position is None:  # pragma: no cover - a run always posts
        return {"run_id": run.run_id, "posted": False}

    hand = run.trail.hand_resolution()
    review = run.trail.review_outcome()

    addends = [
        _line("Cash in bank (confirmed)", position.confirmed_in_bank, sign=""),
        _line("Gateway fee expense", position.fee_expense, sign="+"),
        _line("GST input credit claimable", position.gst_claimable, sign="+"),
        _line("Rounding write-off", position.rounding_writeoff, sign="+"),
    ]
    total = sum(a["paise"] for a in addends)

    in_transit = [
        {
            "ref": e.ref,
            "amount_paise": e.amount_paise,
            "amount": str(Money(e.amount_paise or 0)),
            "value_date": e.value_date.isoformat() if e.value_date else None,
            "what": e.what,
        }
        for e in sorted(
            (x for x in result.exceptions if x.is_in_transit),
            key=lambda x: -(x.amount_paise or 0),
        )
    ]
    in_transit_sum = sum(i["amount_paise"] or 0 for i in in_transit)

    return {
        "run_id": run.run_id,
        "label": run.label(),
        "seed": run.seed,
        "posted": True,
        "closed": run.is_closed,
        "closed_at": run.closed_at.isoformat() if run.closed_at else None,
        # ---- disposition: every rupee is on one of three lines
        "disposition": {
            "auto_posted": {
                "count": position.entries_posted,
                "paise": position.confirmed_in_bank,
                "amount": str(Money(position.confirmed_in_bank)),
            },
            "pending_review": {
                "count": position.pending_review,
                "paise": position.pending_review_paise,
                "amount": str(Money(position.pending_review_paise)),
            },
            "exceptions": {
                "count": position.exceptions,
                "paise": position.exceptions_paise,
                "amount": str(Money(position.exceptions_paise)),
            },
        },
        # ---- the tie-out, as addends plus a total the frontend does not compute
        "tie_out": {
            "addends": addends,
            "total": _line(
                "Revenue recognised", position.revenue_recognised, sign="="
            ),
            "computed_paise": total,
            "ties": total == position.revenue_recognised,
            "delta_paise": total - position.revenue_recognised,
        },
        "in_transit": {
            "count": len(in_transit),
            "total_paise": position.in_transit,
            "total": str(Money(position.in_transit)),
            "ties": in_transit_sum == position.in_transit,
            "items": in_transit,
        },
        "suspense": {
            "paise": position.in_suspense,
            "amount": str(Money(position.in_suspense)),
            "awaiting_approval_paise": position.pending_review_paise,
            "awaiting_approval": str(Money(position.pending_review_paise)),
        },
        # ---- frame 3b: how it closed, read off the trail
        "how_it_closed": {
            "auto_posted": {
                "count": position.entries_posted,
                "paise": position.confirmed_in_bank,
            },
            "approved_in_review": {
                "count": review.approved,
                "paise": review.approved_paise,
            },
            "resolved_by_hand": {
                "count": hand.resolved,
                "paise": hand.resolved_paise,
            },
            "of_those_reversed": {
                "count": hand.reversed_count,
                "paise": hand.reversed_paise,
            },
        },
        "refunds_paise": position.refunds,
        "bank_ledger_total_paise": position.bank_ledger_total,
        "duplicate_postings_refused": result.duplicate_postings_refused,
    }


def _line(label: str, paise: int, *, sign: str) -> dict[str, Any]:
    return {"label": label, "paise": paise, "amount": str(Money(paise)), "sign": sign}


def close_run(run: Any, actor: str) -> dict[str, Any]:
    """"Close 25-Aug" (frame 3b).

    Refused while anything is still open. Closing a period with unresolved
    judgement calls in it is precisely the thing this tool exists to prevent,
    and a button that lets you do it anyway makes the rest of the screen a
    decoration.
    """
    from pipeline.audit import EventType

    position = current_position(run)
    open_count = _still_open(run)
    if open_count:
        raise PeriodNotClearable(
            f"{open_count} exception(s) still need a decision — "
            "in-transit money does not block a close, unresolved money does"
        )
    if run.is_closed:
        return {"closed": True, "closed_at": run.closed_at.isoformat()}

    run.closed_at = run.trail.now()
    run.trail.record(
        EventType.RUN_CLOSED,
        run.run_id,
        actor=actor,
        detail=f"Period closed with {position.in_transit} paise still in transit",
        amount_paise=position.confirmed_in_bank if position else 0,
    )
    return {"closed": True, "closed_at": run.closed_at.isoformat()}


def _still_open(run: Any) -> int:
    """Action-required exceptions nobody has dealt with.

    In-transit rows are excluded by construction, not by a filter someone might
    forget: `is_in_transit` comes from the severity table in Appendix A.
    """
    acted = run.trail.acted_subjects()
    return sum(
        1
        for e in run.result.exceptions
        if not e.is_in_transit and e.ref not in acted
    )


class PeriodNotClearable(ValueError):
    """Asked to close a period that still has open judgement calls in it."""
