"""Classifying what the matchers left behind. Guide §8.2, Appendix A.

The matchers explain bank credits. This explains the other side: ledger rows
that were never settled. A captured sale sitting in no settlement is not an
error — the money left the customer and simply has not landed yet — while an
authorised sale that was never captured is money the customer got back.

**In-transit money is NOT an exception.** Appendix A is explicit about it, and
the distinction is the whole difference between "waiting" and "broken" on a
controller's screen. Folding AWAITING_SETTLEMENT into the exception list would
overstate the problem and understate the cash, and a real controller notices
immediately.

Takes plain records and id sets rather than a MatchContext, so this package
imports nothing beyond `core/` (§3.2). The orchestrator assembles the inputs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.models import Direction, Record
from core.reason_codes import ReasonCode

#: Ledger `status` values, mirroring the payment states of §1.3.
CAPTURED = "captured"
AUTHORIZED = "authorized"
FAILED = "failed"
REFUND = "refund"


@dataclass(frozen=True, slots=True)
class LedgerFinding:
    """One unsettled ledger row, and what it means."""

    ref: str
    reason_code: ReasonCode
    amount_paise: int
    what: str
    why: str

    @property
    def is_in_transit(self) -> bool:
        """True when the money is merely on its way, not missing (Appendix A)."""
        return self.reason_code is ReasonCode.AWAITING_SETTLEMENT


def classify_unsettled_ledger_rows(
    ledger_rows: Iterable[Record],
    *,
    matched_ids: set[str],
    settled_ids: set[str],
) -> list[LedgerFinding]:
    """Explain every sale that no bank credit accounts for.

    `settled_ids` are the orders some settlement report itemises; `matched_ids`
    are the ones a matcher actually tied to a credit. A row in neither has not
    reached the bank, and its ledger status says why.
    """
    findings: list[LedgerFinding] = []

    for row in sorted(ledger_rows, key=lambda r: r.external_id):
        if row.direction is not Direction.INFLOW:
            continue  # refunds are explained by the credit they reduce
        if row.external_id in matched_ids or row.external_id in settled_ids:
            continue

        status = (row.narration or "").strip().lower()
        amount = row.amount.paise

        if status == AUTHORIZED:
            findings.append(
                LedgerFinding(
                    ref=row.external_id,
                    reason_code=ReasonCode.AUTO_REFUNDED,
                    amount_paise=amount,
                    what=(
                        f"{row.external_id} was authorised on {row.value_date} "
                        "but never captured, and no settlement covers it."
                    ),
                    why=(
                        "Payments not captured within three days are refunded "
                        "to the customer automatically. The books show a sale "
                        "that will never settle."
                    ),
                )
            )
        elif status == FAILED:
            findings.append(
                LedgerFinding(
                    ref=row.external_id,
                    reason_code=ReasonCode.LATE_AUTHORIZATION,
                    amount_paise=amount,
                    what=(
                        f"{row.external_id} is marked failed and no credit has "
                        "arrived for it."
                    ),
                    why=(
                        "A payment marked failed on timeout can still be "
                        "authorised later, so this may settle in the next few "
                        "days or may never settle."
                    ),
                )
            )
        else:
            # Captured, so the customer was charged, but nothing has landed.
            findings.append(
                LedgerFinding(
                    ref=row.external_id,
                    reason_code=ReasonCode.AWAITING_SETTLEMENT,
                    amount_paise=amount,
                    what=(
                        f"{row.external_id} was captured on {row.value_date} "
                        "and is not in any settlement yet."
                    ),
                    why=(
                        "When the amount due exceeds the gateway's live "
                        "balance it settles whole transactions and defers the "
                        "rest to the next slot. This is money in transit, not "
                        "money missing."
                    ),
                )
            )

    return findings


def in_transit_total(findings: Iterable[LedgerFinding]) -> int:
    """Real money that has left the customer and not yet landed."""
    return sum(f.amount_paise for f in findings if f.is_in_transit)
