"""Building the cash position from posted entries. Guide §1.6, §4.5.

The value object itself lives in `core/models.py` — it is pure domain, and
`core/` may not import from `posting/`. This module holds the part that knows
about the chart of accounts.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.models import CashPosition, JournalEntry
from posting.chart_of_accounts import Account


def compute_cash_position(
    entries: Iterable[JournalEntry],
    *,
    in_transit: int = 0,
    pending_review: int = 0,
    pending_review_paise: int = 0,
    exceptions: int = 0,
    exceptions_paise: int = 0,
) -> CashPosition:
    """Roll the books up into the one screen a controller opens the tool for."""
    entries = list(entries)
    total = {
        account: sum(e.amount_for(str(account)) for e in entries)
        for account in Account
    }
    # A suspense posting debits BANK too — the money really did arrive — so the
    # BANK ledger balance is the WHOLE statement. "Confirmed" means the part we
    # can explain, which is that balance less what is still in suspense. Stated
    # this way both things hold at once:
    #     confirmed + suspense == the bank statement, to the paise
    #     BANK ledger balance   == the bank statement, to the paise
    suspense_paise = -total[Account.SUSPENSE]
    return CashPosition(
        bank_ledger_total=total[Account.BANK],
        confirmed_in_bank=total[Account.BANK] - suspense_paise,
        in_transit=in_transit,
        in_suspense=suspense_paise,
        revenue_recognised=-total[Account.ACCOUNTS_RECEIVABLE],
        fee_expense=total[Account.GATEWAY_FEE],
        gst_claimable=total[Account.GST_INPUT_CREDIT],
        rounding_writeoff=total[Account.ROUNDING_WRITEOFF],
        refunds=total[Account.REFUNDS],
        entries_posted=sum(1 for e in entries if e.ledger_ids),
        suspense_entries=sum(1 for e in entries if not e.ledger_ids),
        pending_review=pending_review,
        pending_review_paise=pending_review_paise,
        exceptions=exceptions,
        exceptions_paise=exceptions_paise,
    )
