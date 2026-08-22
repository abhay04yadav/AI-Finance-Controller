"""Canonical data model. Guide §3.4.

Everything after L0 speaks this language. Source-specific quirks die at the
adapter boundary. `signed_amount` is why refunds need no special-casing
downstream (§4.3a). `refs` is deliberately a SET of every ID-shaped token found
in the row, not a single typed field (§3.4).

Uses StrEnum, not `(str, Enum)` as written in guide §3.4. On Python 3.11+ the
two differ where it matters: with `(str, Enum)`, `str(Source.BANK)` is
"Source.BANK", not "bank". The guide's own §8.3 example builds a narration with
    f"Unreconciled credit {exc.ref} — {exc.reason_code}"
which would post "ReasonCode.AMOUNT_MISMATCH" into the books. StrEnum makes
str(x) == x.value, so enums are safe in f-strings, narrations, and JSONB.
requires-python is >=3.11, so StrEnum is always available.
"""

from enum import StrEnum


class Source(StrEnum):
    LEDGER = "ledger"
    SETTLEMENT = "settlement"
    BANK = "bank"


class Direction(StrEnum):
    INFLOW = "inflow"  # sale, bank credit
    OUTFLOW = "outflow"  # refund, chargeback, fee


# Record, Candidate, Match, ConfirmedMatch, MatchProposal, MatchContext,
# Ambiguity, Verdict, ExceptionRecord, JournalEntry — Gate 1 onward, §3.4 / §5.2.
