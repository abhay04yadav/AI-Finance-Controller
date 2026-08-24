"""Canonical data model. Guide §3.4.

Everything after L0 speaks this language. Source-specific quirks die at the
adapter boundary, so no layer downstream ever parses a CSV or thinks about
column names.

Uses StrEnum, not `(str, Enum)` as written in §3.4. On Python 3.11+ the two
differ where it matters: with `(str, Enum)`, `str(Source.BANK)` is "Source.BANK",
not "bank". The guide's own §8.3 example builds a narration with
    f"Unreconciled credit {exc.ref} — {exc.reason_code}"
which would post "ReasonCode.AMOUNT_MISMATCH" into the books. StrEnum makes
str(x) == x.value, so enums are safe in f-strings, narrations, and JSONB.
requires-python is >=3.11, so StrEnum is always available.

Candidate, Match, ConfirmedMatch, Ambiguity, Verdict and JournalEntry are named
in §3.2 for this module but are shaped by the layers that produce them; each
arrives at its own gate rather than being guessed at now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from core.money import Money


class Source(StrEnum):
    LEDGER = "ledger"
    SETTLEMENT = "settlement"
    BANK = "bank"


class Direction(StrEnum):
    INFLOW = "inflow"  # sale, bank credit
    OUTFLOW = "outflow"  # refund, chargeback, fee


@dataclass(frozen=True, slots=True)
class SettlementDetail:
    """The money a settlement moved, typed. Guide §4.1, §4.2.

    `Record.amount` carries the NET, because that is what actually lands in the
    bank and what has to tie to the statement. But two layers need more than the
    net, and both are load-bearing:

        L1  asserts  sum(ledger gross) == settlement gross   (§4.1 step 4)
        L2  infers   r = (1 - net/gross) / 1.18              (§4.2)

    Reaching into an untyped `raw` dict for those would put the fee model's only
    input behind `dict[str, Any]`, where mypy cannot see a typo and a wrong key
    silently yields a wrong rate — which then quietly mis-matches every credit
    downstream. So they are fields, checked by mypy --strict.
    """

    gross: Money
    fee: Money
    gst: Money
    net: Money
    utr: str
    order_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.gross.paise < 0:
            raise ValueError(f"settlement gross is negative: {self.gross}")
        if self.net.paise > self.gross.paise:
            raise ValueError(
                f"settlement nets more than gross ({self.net} > {self.gross})"
            )

    @property
    def charges(self) -> Money:
        """MDR plus GST — what the gateway kept."""
        return self.fee + self.gst

    @property
    def unitemised_paise(self) -> int:
        """Money deducted beyond the stated charges.

        Non-zero when a cross-period refund was netted out of this settlement
        without appearing among its order rows (§4.3b). It is the size of what
        L3 still has to explain.
        """
        return self.gross.paise - self.charges.paise - self.net.paise

    def implied_fee_rate(self, gst_rate: float) -> float:
        """Invert the fee model for this one settlement (§4.2).

        Only meaningful where nothing unitemised was deducted — otherwise the
        refund is mistaken for fee. L2 filters on that before taking a median.
        """
        if self.gross.paise <= 0:
            raise ValueError("cannot infer a rate from a zero gross")
        return (1 - self.net.paise / self.gross.paise) / (1 + gst_rate)


@dataclass(frozen=True, slots=True)
class Record:
    """One normalized row from any of the three sources.

    `raw` is excluded from equality and repr so that two records parsed from the
    same logical row compare equal regardless of incidental source formatting,
    and so `Record` stays hashable despite carrying a dict.
    """

    source: Source
    external_id: str  # ORD-101 | SETL-88 | UTR-77291
    amount: Money  # ALWAYS int paise inside
    value_date: date  # business date, Asia/Kolkata
    direction: Direction
    narration: str = ""
    refs: frozenset[str] = frozenset()  # every ID-shaped token found anywhere
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)
    #: Typed money for settlement rows; None for every other source.
    detail: SettlementDetail | None = None

    def __post_init__(self) -> None:
        if self.source is Source.SETTLEMENT and self.detail is None:
            raise ValueError(
                f"settlement record {self.external_id} has no typed detail — "
                "L1 and L2 would have to read money out of `raw`"
            )
        if self.source is not Source.SETTLEMENT and self.detail is not None:
            raise ValueError(
                f"{self.source} record {self.external_id} carries settlement detail"
            )

    def settlement(self) -> SettlementDetail:
        """The typed settlement money, or a loud failure.

        Lets L1 and L2 read `record.settlement().gross` without a None-check at
        every call site, while still refusing to invent a value.
        """
        if self.detail is None:
            raise TypeError(f"{self.external_id} is not a settlement record")
        return self.detail

    @property
    def signed_amount(self) -> int:
        """Amount in paise, negative for outflows.

        This single property is why refunds need no special-casing downstream
        (§4.3a): a refund enters the L3 candidate pool as a negative number and
        flows through the identical solver. No branch, no `if is_refund:`.
        """
        return (
            self.amount.paise
            if self.direction is Direction.INFLOW
            else -self.amount.paise
        )

    @property
    def is_inflow(self) -> bool:
        return self.direction is Direction.INFLOW

    def __str__(self) -> str:
        arrow = "+" if self.is_inflow else "-"
        return f"{self.source}:{self.external_id} {arrow}{self.amount} {self.value_date}"


@dataclass(frozen=True, slots=True)
class MatchProposal:
    """One strategy's claim that a bank credit is explained by these ledger rows.

    A proposal is not yet a match: the orchestrator accepts it, and only records
    still unclaimed are passed to the next strategy (§5.4, chain of
    responsibility). Every proposal carries the reason it was made and the
    evidence it rested on, because no automated decision may post without one
    (§2.7 rule 4).
    """

    bank_utr: str
    ledger_ids: frozenset[str]
    confidence: float
    strategy: str
    reason: str
    evidence: tuple[str, ...] = ()
    settlement_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
        if not self.ledger_ids:
            raise ValueError(f"proposal for {self.bank_utr} explains nothing")
        if not self.reason.strip():
            raise ValueError(f"proposal for {self.bank_utr} carries no reason")


@dataclass(frozen=True, slots=True)
class JournalLine:
    """One side of one account movement. Exactly one of debit/credit is set."""

    account: str
    debit_paise: int = 0
    credit_paise: int = 0

    def __post_init__(self) -> None:
        if self.debit_paise and self.credit_paise:
            raise ValueError(f"{self.account} line is both a debit and a credit")
        if self.debit_paise < 0 or self.credit_paise < 0:
            raise ValueError(
                f"{self.account} line carries a negative amount — flip the side instead"
            )


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A balanced double-entry posting. Guide §4.5, §9.4.

    `assert_balanced()` runs before anything can be persisted. A reconciliation
    tool that produces unbalanced books is worse than none, so an unbalanced
    entry is a bug that raises rather than a value that flows.
    """

    idempotency_key: str
    entry_date: date
    narration: str
    lines: tuple[JournalLine, ...]
    #: Provenance, for the audit trail (§9.3): who decided this, on what.
    source_utr: str = ""
    ledger_ids: frozenset[str] = frozenset()
    settlement_id: str | None = None
    confidence: float = 0.0
    strategy: str = ""

    def __post_init__(self) -> None:
        if not self.narration.strip():
            # §2.7 rule 4 / §4.5: no entry may post without a justification.
            raise ValueError(f"entry {self.idempotency_key} has no narration")
        if not self.lines:
            raise ValueError(f"entry {self.idempotency_key} has no lines")

    @property
    def total_debits(self) -> int:
        return sum(line.debit_paise for line in self.lines)

    @property
    def total_credits(self) -> int:
        return sum(line.credit_paise for line in self.lines)

    def assert_balanced(self) -> None:
        if self.total_debits != self.total_credits:
            raise ValueError(
                f"unbalanced entry {self.idempotency_key}: "
                f"debits {self.total_debits} != credits {self.total_credits}"
            )

    def amount_for(self, account: str) -> int:
        """Signed movement on one account: positive debit, negative credit."""
        return sum(
            line.debit_paise - line.credit_paise
            for line in self.lines
            if line.account == account
        )


@dataclass(frozen=True, slots=True)
class CashPosition:
    """What is in the bank, what is coming, and what is not understood."""

    #: Money we can explain: the bank ledger less whatever sits in suspense.
    confirmed_in_bank: int = 0
    #: The whole BANK ledger balance, which must equal the bank statement.
    bank_ledger_total: int = 0
    in_transit: int = 0
    in_suspense: int = 0
    revenue_recognised: int = 0
    fee_expense: int = 0
    gst_claimable: int = 0
    rounding_writeoff: int = 0
    refunds: int = 0

    #: Entries that explain a match. Suspense postings are counted
    #: separately: they are money parked, not books closed.
    entries_posted: int = 0
    suspense_entries: int = 0
    pending_review: int = 0
    pending_review_paise: int = 0
    exceptions: int = 0
    exceptions_paise: int = 0

    @property
    def unreconciled_paise(self) -> int:
        """The one number that answers "how much do I still not understand?"."""
        return self.in_suspense
