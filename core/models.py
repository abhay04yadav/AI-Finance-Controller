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
