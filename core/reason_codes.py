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
    #: none was confirmed. Three ways to arrive here, and the card's `why` says
    #: which: --no-llm is in force (§4.4, NullAdjudicator); no API credential was
    #: available; or an adjudicator was asked, examined every combination and
    #: declined to choose because the evidence supported none of them.
    #:
    #: The code describes the CREDIT's state — still ambiguous, still unresolved
    #: — not whether a model was consulted. An abstention is a real answer and
    #: keeps this code precisely because nothing was matched and no entry was
    #: prepared; only a verdict thrown out by a guardrail becomes
    #: ADJUDICATION_REJECTED.
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


class Disposition(StrEnum):
    """What SHOULD happen to an anomaly of this kind.

    Not every planted anomaly is meant to end up on the exception page. A
    settlement pushed off a Sunday, a refund netted from a later batch, a
    payment that authorised late, a fee rounded a paisa differently — these are
    the hard cases the matcher was built to absorb silently. Surfacing one is a
    failure of the matcher, not a success of the exception list.

    Others cannot be resolved by anyone: a duplicated credit line, revenue the
    books never recorded, a sale refunded back to the customer. Those must reach
    a human.

    Scoring both against one "exception recall" number counts correct behaviour
    as failure. See `eval/metrics.py` for how the two are reported apart.
    """

    #: The matcher is expected to absorb this without a human seeing it.
    RESOLVABLE = "resolvable"
    #: No amount of matching can settle this; a person has to decide.
    MUST_SURFACE = "must_surface"


_RESOLVABLE: frozenset[ReasonCode] = frozenset(
    {
        # L3's wider refund window exists precisely for this.
        ReasonCode.CROSS_PERIOD_REFUND,
        # The business-day calendar exists precisely for this.
        ReasonCode.HOLIDAY_SHIFT,
        # The money arrived; only the ledger status is stale.
        ReasonCode.LATE_AUTHORIZATION,
        # Tolerance and the write-off account exist precisely for this.
        ReasonCode.ROUNDING_DRIFT,
    }
)


def disposition_of(code: ReasonCode) -> Disposition:
    return (
        Disposition.RESOLVABLE
        if code in _RESOLVABLE
        else Disposition.MUST_SURFACE
    )


def is_resolvable(code: ReasonCode) -> bool:
    return disposition_of(code) is Disposition.RESOLVABLE

