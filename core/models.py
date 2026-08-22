"""Canonical data model. Guide §3.4.

Everything after L0 speaks this language. Source-specific quirks die at the
adapter boundary. `signed_amount` is why refunds need no special-casing
downstream (§4.3a). `refs` is deliberately a SET of every ID-shaped token found
in the row, not a single typed field (§3.4).
"""

from enum import Enum


class Source(str, Enum):
    LEDGER = "ledger"
    SETTLEMENT = "settlement"
    BANK = "bank"


class Direction(str, Enum):
    INFLOW = "inflow"  # sale, bank credit
    OUTFLOW = "outflow"  # refund, chargeback, fee


# Record, Candidate, Match, ConfirmedMatch, MatchProposal, MatchContext,
# Ambiguity, Verdict, ExceptionRecord, JournalEntry — Gate 1 onward, §3.4 / §5.2.
