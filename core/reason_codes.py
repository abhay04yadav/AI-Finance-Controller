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

from collections.abc import Iterable
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


#: Root cause beats symptom. Guide §8.2.
#:
#: One credit can trip several checks at once, and on seed 42 two of them do:
#: a settlement whose orders are absent from the ledger raises
#: MISSING_IN_LEDGER from the coverage check AND AMOUNT_MISMATCH from L3, which
#: could not explain the credit precisely BECAUSE the orders are missing. Both
#: sentences are true. Showing both is still wrong: it puts one problem on two
#: cards, tells a controller there are nine when there are seven, and — because
#: the header sums the cards — overstates the unreconciled figure by the
#: duplicated amount. On seed 42 that was Rs 41,959.94 of a stated Rs 1,50,918.37.
#:
#: Lower number wins. The ordering is by explanatory power: a code that says
#: WHY the money cannot be matched outranks one that only says THAT it cannot.
_PRECEDENCE: dict[ReasonCode, int] = {
    # The row could not be read. Nothing downstream means anything.
    ReasonCode.INGEST_ERROR: 0,
    # The statement itself is at fault, before any matching is attempted.
    ReasonCode.DUPLICATE_UTR: 1,
    # The counterparty rows do not exist. Everything else follows from this.
    ReasonCode.MISSING_IN_LEDGER: 2,
    # A model was asked and its answer was thrown out.
    ReasonCode.ADJUDICATION_REJECTED: 3,
    # Several combinations fit and nothing separated them.
    ReasonCode.AMBIGUOUS_UNADJUDICATED: 4,
    # Named causes: each says something specific about the gap.
    ReasonCode.FX_OR_SLAB_VARIANCE: 5,
    ReasonCode.CROSS_PERIOD_REFUND: 6,
    ReasonCode.HOLIDAY_SHIFT: 7,
    ReasonCode.LATE_AUTHORIZATION: 8,
    ReasonCode.AUTO_REFUNDED: 9,
    ReasonCode.ROUNDING_DRIFT: 10,
    # The catch-all. True whenever any of the above is true, so it loses to
    # all of them: "nothing adds up" is the least useful thing we can say.
    ReasonCode.AMOUNT_MISMATCH: 11,
    # Not a failure at all, and never in competition — in-transit rows are
    # kept in their own collection (Appendix A).
    ReasonCode.AWAITING_SETTLEMENT: 99,
}


def precedence_of(code: ReasonCode) -> int:
    """How strongly this code explains a credit. Lower is more explanatory."""
    return _PRECEDENCE.get(code, 50)


def most_explanatory(codes: "Iterable[ReasonCode]") -> ReasonCode:
    """Of several codes raised against one reference, the one to show."""
    return min(codes, key=precedence_of)


#: One sentence per code, in the words a controller would use. Guide §8.2.
#:
#: The reason code is a category, not an explanation: MISSING_IN_LEDGER tells a
#: developer exactly what fired and tells the person who has to fix it almost
#: nothing. Design 4a puts a plain sentence on every row for that reason.
#:
#: These live HERE and not in a lookup table in a React component, for the same
#: reason `available_for()` does: adding a reason code should bring its own
#: sentence with it, and a screen should never be able to describe a code the
#: registry does not have. The frontend renders whatever the API returns.
_PLAIN_ENGLISH: dict[ReasonCode, str] = {
    ReasonCode.AWAITING_SETTLEMENT: (
        "The customer has paid; the money is still on its way to the bank."
    ),
    ReasonCode.LATE_AUTHORIZATION: (
        "The card was charged days after the customer agreed to pay."
    ),
    ReasonCode.AUTO_REFUNDED: (
        "The sale was authorised but never captured, so the money went back."
    ),
    ReasonCode.CROSS_PERIOD_REFUND: (
        "A refund for a sale in a month whose books are already closed."
    ),
    ReasonCode.HOLIDAY_SHIFT: (
        "A bank holiday pushed the payment past the day we expected it."
    ),
    ReasonCode.DUPLICATE_UTR: (
        "The bank listed the same payment twice, so the books count it twice."
    ),
    ReasonCode.MISSING_IN_LEDGER: (
        "Money arrived for a sale we have no record of making."
    ),
    ReasonCode.ROUNDING_DRIFT: (
        "Sub-paisa differences, each harmless, that add up to a real gap."
    ),
    ReasonCode.AMOUNT_MISMATCH: (
        "Money arrived, but no combination of orders adds up to it."
    ),
    ReasonCode.FX_OR_SLAB_VARIANCE: (
        "The gateway kept a larger share of this sale than our model expects."
    ),
    ReasonCode.AMBIGUOUS_UNADJUDICATED: (
        "Several different sets of orders explain this payment equally well."
    ),
    ReasonCode.ADJUDICATION_REJECTED: (
        "An adjudicator answered and the answer failed its checks, so it was "
        "discarded."
    ),
    ReasonCode.INGEST_ERROR: (
        "A row in the source file could not be read."
    ),
}


#: The four-to-six word label a row leads with, in a controller's words.
#:
#: Distinct from `_PLAIN_ENGLISH`, and both are on the row: the title is what
#: the eye lands on scanning forty rows, the sentence is what it reads once it
#: has stopped. A title long enough to be a sentence would collapse the two
#: back into one and the scan would be gone.
_TITLE: dict[ReasonCode, str] = {
    ReasonCode.AWAITING_SETTLEMENT: "On its way to the bank",
    ReasonCode.LATE_AUTHORIZATION: "Charged days after the sale",
    ReasonCode.AUTO_REFUNDED: "Refunded before we could match it",
    ReasonCode.CROSS_PERIOD_REFUND: "Refund against a closed period",
    ReasonCode.HOLIDAY_SHIFT: "A bank holiday moved the date",
    ReasonCode.DUPLICATE_UTR: "The bank listed one payment twice",
    ReasonCode.MISSING_IN_LEDGER: "Money with no matching sale",
    ReasonCode.ROUNDING_DRIFT: "Sub-paisa drift, added up",
    ReasonCode.AMOUNT_MISMATCH: "Nothing adds up to this credit",
    ReasonCode.FX_OR_SLAB_VARIANCE: "The gateway kept more than we model",
    ReasonCode.AMBIGUOUS_UNADJUDICATED: "Several answers fit equally well",
    ReasonCode.ADJUDICATION_REJECTED: "The adjudicator's answer failed its checks",
    ReasonCode.INGEST_ERROR: "A source row could not be read",
}


def title_of(code: ReasonCode) -> str:
    """The short label a row leads with."""
    return _TITLE.get(code, "Could not be reconciled")


def plain_english_of(code: ReasonCode) -> str:
    """The row's one-line explanation, for someone who is not a developer."""
    return _PLAIN_ENGLISH.get(code, "This payment could not be reconciled.")
