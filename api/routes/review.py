"""The review queue, and the two decisions that empty it. Guide §4.5, §8.4.

Frame 2b's rule, and the reason this file exists rather than a boolean on the
frontend: **an entry whose debits do not equal its credits must be impossible to
approve.** Greying out a button is a suggestion; refusing in the handler is a
guarantee. The check runs here, and again inside `JournalEntry.assert_balanced`
when the entry is built, and a third time in the repository before it is stored.
Three times is not paranoia — it is the one invariant that makes the books worth
reading (§9.4).

Approval does not post the prepared entry as written. The credit is already in
the books as `Dr BANK / Cr SUSPENSE`, so re-posting it would bank the same money
twice; `approval_entry()` swaps the bank debit for a suspense debit, which nets
the holding to zero. That subtlety belongs in `posting/`, and this module simply
refuses to work around it.
"""

from __future__ import annotations

from typing import Any, cast

from core.money import Money
from core.run_result import ReviewItem
from posting.chart_of_accounts import Account
from posting.journal_builder import approval_entry


def serialize_entry(entry: Any, number: str | None = None) -> dict[str, Any]:
    """A prepared or posted journal entry, as frame 2b's table renders it."""
    return {
        "number": number,
        "idempotency_key": entry.idempotency_key,
        "entry_date": entry.entry_date.isoformat(),
        "narration": entry.narration,
        "lines": [
            {
                "account": line.account,
                "debit_paise": line.debit_paise,
                "credit_paise": line.credit_paise,
            }
            for line in entry.lines
        ],
        "total_debits_paise": entry.total_debits,
        "total_credits_paise": entry.total_credits,
        "balanced": entry.total_debits == entry.total_credits,
        "source_utr": entry.source_utr,
        "ledger_ids": sorted(entry.ledger_ids),
        "settlement_id": entry.settlement_id,
        "confidence": entry.confidence,
        "strategy": entry.strategy,
    }


def cash_awaiting(item: ReviewItem) -> int:
    """What actually lands in the bank if this is approved.

    NOT `total_debits`. An entry's debits include the gateway fee, the GST and
    any rounding line, so summing them overstates the queue by exactly the fees
    — ₹6,944.22 across seed 42's eleven entries. `/books` reports the bank
    figure, and a header on `/review` that disagreed with it would be two
    screens quoting different numbers for the same money.
    """
    return item.prepared_entry.amount_for(str(Account.BANK))


def serialize_review_item(item: ReviewItem, decision: str | None) -> dict[str, Any]:
    entry = item.prepared_entry
    landing = cash_awaiting(item)
    return {
        "utr": item.utr,
        "ledger_ids": sorted(item.ledger_ids),
        "order_count": len(item.ledger_ids),
        "confidence": item.confidence,
        "reason": item.reason,
        "amount_paise": landing,
        "amount": str(Money(landing)),
        #: The entry's own total, which is what its table foots to. Different
        #: from `amount_paise` by the fee and GST, and both are shown.
        "entry_total_paise": entry.total_debits,
        "value_date": entry.entry_date.isoformat(),
        "settlement_id": entry.settlement_id,
        "prepared_entry": serialize_entry(entry),
        "decision": decision,
    }


def review_payload(run: Any) -> dict[str, Any]:
    """Frame 2b: the header total, the confidence band, and the queue.

    `band_precision` is the calibration table's answer for this band, and it is
    the sentence that makes the queue a nod rather than an investigation. It is
    read from the run, never asserted — if the band ever stops scoring 100% the
    copy has to change with it.
    """
    result = run.result
    items = [
        i for i in result.review_queue if run.review_decisions.get(i.utr) is None
    ]
    decided = [
        i for i in result.review_queue if run.review_decisions.get(i.utr) is not None
    ]
    pending_paise = sum(cash_awaiting(i) for i in items)

    settings = run.settings
    return {
        "run_id": run.run_id,
        "count": len(items),
        "total_paise": pending_paise,
        "total": str(Money(pending_paise)),
        "auto_post_threshold": settings.auto_post_threshold,
        "review_threshold": settings.review_threshold,
        "items": [
            serialize_review_item(i, run.review_decisions.get(i.utr))
            for i in sorted(items, key=lambda i: (-cash_awaiting(i), i.utr))
        ],
        "decided": [
            serialize_review_item(i, run.review_decisions.get(i.utr)) for i in decided
        ],
        "fee_rate": result.fee_rate,
        "gst_rate": settings.gst_rate,
    }


class Unbalanced(ValueError):
    """A prepared entry that does not balance. Refused, never posted (§9.4)."""


class AlreadyDecided(ValueError):
    """Approving something already approved is a no-op, not a second posting."""


def approve(run: Any, utr: str, actor: str) -> dict[str, Any]:
    """Post the approved entry, or refuse it.

    Returns the journal number the book issued, which is what the UI shows the
    controller — "posted as JE-0061" is a receipt; "approved ✓" is a rumour.
    """
    from pipeline.audit import EventType

    item = _find(run, utr)
    if run.review_decisions.get(utr) is not None:
        raise AlreadyDecided(f"{utr} was already {run.review_decisions[utr]}")

    prepared = item.prepared_entry
    if prepared.total_debits != prepared.total_credits:
        # The gate-12 requirement, enforced server-side. A frontend that had
        # somehow offered the button still cannot post through it.
        raise Unbalanced(
            f"{utr}: debits {prepared.total_debits} != credits "
            f"{prepared.total_credits} — refusing to post"
        )

    item_cash = cash_awaiting(item)
    entry = approval_entry(prepared)
    posted = run.repository.post(entry)
    number = run.repository.number_for(entry.idempotency_key)
    run.review_decisions[utr] = "approved"

    run.trail.record(
        EventType.REVIEW_APPROVED,
        utr,
        actor=actor,
        detail=item.reason,
        entry_numbers=[number] if number else [],
        amount_paise=item_cash,
    )
    return {
        "utr": utr,
        "decision": "approved",
        "posted": posted,
        "entry_number": number,
        "entry": serialize_entry(entry, number),
    }


def reject(run: Any, utr: str, actor: str) -> dict[str, Any]:
    """Send the credit back to the exception list.

    Nothing is posted and nothing is un-posted: the money is already sitting in
    suspense, which is exactly where a credit nobody has confirmed belongs. What
    changes is that a person has now looked at it and declined to confirm it.
    """
    from pipeline.audit import EventType

    item = _find(run, utr)
    if run.review_decisions.get(utr) is not None:
        raise AlreadyDecided(f"{utr} was already {run.review_decisions[utr]}")

    run.review_decisions[utr] = "rejected"
    run.trail.record(
        EventType.REVIEW_REJECTED,
        utr,
        actor=actor,
        detail=f"Rejected in review: {item.reason}",
        amount_paise=cash_awaiting(item),
    )
    return {"utr": utr, "decision": "rejected", "posted": False, "entry_number": None}


def _find(run: Any, utr: str) -> ReviewItem:
    for item in run.result.review_queue:
        if item.utr == utr:
            return cast(ReviewItem, item)
    raise KeyError(utr)
