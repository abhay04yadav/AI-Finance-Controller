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
