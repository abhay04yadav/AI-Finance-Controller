"""Reason code registry. Guide Appendix A, §1.5.

Note on AWAITING_SETTLEMENT: it is NOT a failure. The money is genuinely still
in transit. It gets its own visual treatment, separate from true exceptions.
"""

from enum import Enum


class ReasonCode(str, Enum):
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
