"""Reason code registry. Guide Appendix A, §1.5.

Note on AWAITING_SETTLEMENT: it is NOT a failure. The money is genuinely still
in transit. It gets its own visual treatment, separate from true exceptions.

Uses StrEnum, not `(str, Enum)` as written in guide §3.4. On Python 3.11+ the
two differ where it matters: with `(str, Enum)`, `str(Source.BANK)` is
"Source.BANK", not "bank". The guide's own §8.3 example builds a narration with
    f"Unreconciled credit {exc.ref} — {exc.reason_code}"
which would post "ReasonCode.AMOUNT_MISMATCH" into the books. StrEnum makes
str(x) == x.value, so enums are safe in f-strings, narrations, and JSONB.
requires-python is >=3.11, so StrEnum is always available.
"""

from enum import StrEnum


class ReasonCode(StrEnum):
    """The twelve codes from Appendix A. Values filled at Gate 1."""

    AWAITING_SETTLEMENT = "AWAITING_SETTLEMENT"  # in transit — NOT an error
    LATE_AUTHORIZATION = "LATE_AUTHORIZATION"
    AUTO_REFUNDED = "AUTO_REFUNDED"
    CROSS_PERIOD_REFUND = "CROSS_PERIOD_REFUND"
    HOLIDAY_SHIFT = "HOLIDAY_SHIFT"
    DUPLICATE_UTR = "DUPLICATE_UTR"
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    ROUNDING_DRIFT = "ROUNDING_DRIFT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    FX_OR_SLAB_VARIANCE = "FX_OR_SLAB_VARIANCE"
    ADJUDICATION_REJECTED = "ADJUDICATION_REJECTED"
    INGEST_ERROR = "INGEST_ERROR"
