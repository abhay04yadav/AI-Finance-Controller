"""Double-entry construction. Builder pattern. Guide §4.5, §5.3, §9.4.

A matched ₹8,000 order does not post as "₹8,000 received". It decomposes:

    Bank Account              Dr.  ₹7,811.20      what actually arrived
    Gateway Fee (expense)     Dr.  ₹  160.00      MDR
    GST Input Credit          Dr.  ₹   28.80      reclaimable tax
            To  Accounts Receivable    ₹8,000.00  what the customer owed

**The GST line is not cosmetic.** Collapsed into the fee, the merchant silently
forfeits reclaimable input credit — real money over a year, and a concrete
business win to be able to point at.

**BANK takes the actual credit, never a computed one.** §4.5's sketch debits
`gross - fee - gst`, but the gateway rounds the fee and then rounds GST on the
rounded fee, so that figure disagrees with the statement on 52 of 53 settlements
here — by one or two paise each. Posting it would mean the books never tie to
the bank to the paise, which is the §4.5 acceptance test. The difference gets
its own line instead, on the rounding write-off account Appendix B provides for
exactly this.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date

from core.models import JournalEntry, JournalLine
from posting.chart_of_accounts import Account


class JournalEntryBuilder:
    """Accumulate lines, then `build()` — which refuses to return an unbalanced entry."""

    def __init__(self) -> None:
        self._lines: list[JournalLine] = []
        self._narration = ""
        self._date: date | None = None
        self._key = ""
        self._utr = ""
        self._ledger_ids: frozenset[str] = frozenset()
        self._settlement_id: str | None = None
        self._confidence = 0.0
        self._strategy = ""

    def debit(self, account: Account, paise: int) -> JournalEntryBuilder:
        if paise:
            self._lines.append(JournalLine(str(account), debit_paise=paise))
        return self

    def credit(self, account: Account, paise: int) -> JournalEntryBuilder:
        if paise:
            self._lines.append(JournalLine(str(account), credit_paise=paise))
        return self

    def signed(self, account: Account, paise: int) -> JournalEntryBuilder:
        """Debit when positive, credit when negative. Lets a residual of unknown
        sign be posted without the caller branching on it."""
        return self.debit(account, paise) if paise > 0 else self.credit(account, -paise)

    def on(self, entry_date: date) -> JournalEntryBuilder:
        self._date = entry_date
        return self

    def because(self, narration: str) -> JournalEntryBuilder:
        self._narration = narration
        return self

    def traced_to(
        self,
        *,
        utr: str = "",
        ledger_ids: Iterable[str] = (),
        settlement_id: str | None = None,
        confidence: float = 0.0,
        strategy: str = "",
    ) -> JournalEntryBuilder:
        self._utr = utr
        self._ledger_ids = frozenset(ledger_ids)
        self._settlement_id = settlement_id
        self._confidence = confidence
        self._strategy = strategy
        return self

    def build(self) -> JournalEntry:
        if self._date is None:
            raise ValueError("journal entry has no date")
        entry = JournalEntry(
            idempotency_key=self._key
            or idempotency_key(self._ledger_ids, self._utr, self._settlement_id),
            entry_date=self._date,
            narration=self._narration,
            lines=tuple(self._lines),
            source_utr=self._utr,
            ledger_ids=self._ledger_ids,
            settlement_id=self._settlement_id,
            confidence=self._confidence,
            strategy=self._strategy,
        )
        # Never hand back something that could be persisted unbalanced (§9.4).
        entry.assert_balanced()
        return entry


def idempotency_key(
    ledger_ids: Iterable[str], utr: str, settlement_id: str | None = None
) -> str:
    """§4.5: sha256 of the sorted ledger ids, the UTR and the settlement id.

    Derived from *what the entry is about*, never from when it was posted, so
    the same reconciliation run twice produces the same key and the second post
    is a no-op rather than a duplicate.
    """
    payload = "|".join(
        [",".join(sorted(ledger_ids)), utr, settlement_id or ""]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_settlement_entry(
    *,
    entry_date: date,
    utr: str,
    gross_orders_paise: int,
    refunds_paise: int,
    fee_paise: int,
    gst_paise: int,
    actual_credit_paise: int,
    narration: str,
    ledger_ids: Iterable[str],
    settlement_id: str | None = None,
    confidence: float = 0.0,
    strategy: str = "",
) -> JournalEntry:
    """The four-line entry above, plus whatever the specific case needs.

    Refunds get their own line rather than being netted into the receivable: a
    controller reading the books should see what was sold and what was returned,
    not a single smaller number.

    The residual absorbs per-step gateway rounding so that BANK always carries
    the figure the statement carries.
    """
    residual = (
        gross_orders_paise
        - fee_paise
        - gst_paise
        - refunds_paise
        - actual_credit_paise
    )

    return (
        JournalEntryBuilder()
        .on(entry_date)
        .because(narration)
        .traced_to(
            utr=utr,
            ledger_ids=ledger_ids,
            settlement_id=settlement_id,
            confidence=confidence,
            strategy=strategy,
        )
        .debit(Account.BANK, actual_credit_paise)
        .debit(Account.GATEWAY_FEE, fee_paise)
        .debit(Account.GST_INPUT_CREDIT, gst_paise)
        .debit(Account.REFUNDS, refunds_paise)
        .signed(Account.ROUNDING_WRITEOFF, residual)
        .credit(Account.ACCOUNTS_RECEIVABLE, gross_orders_paise)
        .build()
    )


def build_suspense_entry(
    *, entry_date: date, utr: str, paise: int, narration: str, occurrence: int = 1
) -> JournalEntry:
    """Money that arrived and cannot yet be explained. Guide §4.5.

    Unmatched credits are still real money in the bank, so they are posted —
    not ignored. That is what keeps the bank balance in the books equal to the
    bank balance at the bank, and it makes the suspense total the one number a
    controller reads as "how much do I still not understand?"
    """
    builder = JournalEntryBuilder()
    # A duplicated UTR is two lines on the statement, so it must become two
    # entries — otherwise the second is refused as a repost and the books come
    # up short by exactly the duplicated amount. The occurrence keeps the keys
    # distinct without making them time-dependent.
    builder._key = idempotency_key([f"occurrence:{occurrence}"], utr, None)
    return (
        builder.on(entry_date)
        .because(narration)
        .traced_to(utr=utr)
        .debit(Account.BANK, paise)
        .credit(Account.SUSPENSE, paise)
        .build()
    )


def approval_entry(prepared: JournalEntry) -> JournalEntry:
    """Turn a prepared review entry into the one that posts on approval.

    The credit is ALREADY in the books: when it could not be explained it was
    posted `Dr BANK / Cr SUSPENSE`, which is what keeps the bank balance equal
    to the statement while a human decides. Posting the prepared entry as-is
    would debit BANK a second time and count the same money twice.

    So approval clears the holding instead of re-banking the cash: the BANK
    debit becomes a SUSPENSE debit, which nets the earlier credit to zero and
    leaves the explanation — fee, GST, refunds, receivable — in its place.

        before   Dr BANK 7,811.20            Cr SUSPENSE 7,811.20
        approve  Dr SUSPENSE 7,811.20 ...    Cr A/R 8,000.00
        net      Dr BANK 7,811.20  Dr fee/GST  Cr A/R 8,000.00   suspense zero
    """
    bank, suspense = str(Account.BANK), str(Account.SUSPENSE)
    lines = tuple(
        JournalLine(suspense, debit_paise=line.debit_paise)
        if line.account == bank and line.debit_paise
        else line
        for line in prepared.lines
    )
    entry = JournalEntry(
        idempotency_key=idempotency_key(
            [*sorted(prepared.ledger_ids), "approved"],
            prepared.source_utr,
            prepared.settlement_id,
        ),
        entry_date=prepared.entry_date,
        narration=f"Approved after review: {prepared.narration}",
        lines=lines,
        source_utr=prepared.source_utr,
        ledger_ids=prepared.ledger_ids,
        settlement_id=prepared.settlement_id,
        confidence=prepared.confidence,
        strategy=prepared.strategy,
    )
    entry.assert_balanced()
    return entry

