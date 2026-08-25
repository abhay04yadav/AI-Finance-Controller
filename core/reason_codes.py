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
    #: L3 found several combinations that each explain the credit exactly, and
    #: no adjudicator was available to choose between them — either because L4
    #: is not built yet, or because --no-llm is in force (§4.4, NullAdjudicator).
    #:
    #: NOT in Appendix A, deliberately. Appendix A gives ADJUDICATION_REJECTED
    #: for "the LLM answered and a guardrail threw the answer out". Reusing it
    #: here would make the exception page say the AI tried and failed on runs
    #: where no AI ran at all — a false sentence in our own UI, and precisely the
    #: opposite of the honest exception list the brief asks for. It also keeps
    #: "the model was wrong" measurable separately from "the model was never
    #: asked", which is what makes guardrail effectiveness a real number at
    #: gate 11.
    AMBIGUOUS_UNADJUDICATED = "AMBIGUOUS_UNADJUDICATED"
    ADJUDICATION_REJECTED = "ADJUDICATION_REJECTED"
    INGEST_ERROR = "INGEST_ERROR"


class Severity(StrEnum):
    """How a finding should be presented to a controller.

    The distinction is not cosmetic. Money in transit is money that has left the
    customer and will arrive; an exception is money nobody can account for. A
    screen that shows them the same way tells a controller their books are
    broken when they are merely waiting, and a real one notices immediately.
    """

    IN_TRANSIT = "in_transit"
    ACTION_REQUIRED = "action_required"


#: Appendix A: AWAITING_SETTLEMENT is NOT a failure. Everything else is
#: something a human has to decide about.
_IN_TRANSIT: frozenset[ReasonCode] = frozenset({ReasonCode.AWAITING_SETTLEMENT})


def severity_of(code: ReasonCode) -> Severity:
    return Severity.IN_TRANSIT if code in _IN_TRANSIT else Severity.ACTION_REQUIRED


def is_in_transit(code: ReasonCode) -> bool:
    return severity_of(code) is Severity.IN_TRANSIT

