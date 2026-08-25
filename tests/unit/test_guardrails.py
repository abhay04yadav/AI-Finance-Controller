"""The three guardrails. Guide §4.4, Review Guide gate 11.

    LLM SELECTS. CODE VERIFIES. ALWAYS.

Every test in this file runs with no API key, no network and no dataset. That is
the point of putting the contract in `core/` and the checks in a pure function:
the thing standing between a model's answer and the general ledger is testable
in milliseconds, so it is tested exhaustively rather than sampled.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adjudication import guardrails
from adjudication.guardrails import (
    ARITHMETIC_MISMATCH,
    HALLUCINATED_CANDIDATE,
    MISSING_HYPOTHESIS,
    MISSING_REASON,
    UNKNOWN_ACTION,
    UNKNOWN_CLASSIFICATION,
    verify,
    verify_hypothesis,
)
from core.adjudication import (
    NO_SELECTION,
    Ambiguity,
    Candidate,
    CandidateLeg,
    Hypothesis,
    Unexplained,
    Verdict,
)

GOOD_REASON = "Narration carries SETL88, matching every settlement_id in A."


def leg(order_id: str, paise: int, *, settlement: str | None = "SETL-88") -> CandidateLeg:
    return CandidateLeg(
        order_id=order_id,
        gross_equivalent_paise=paise,
        capture_date=date(2026, 8, 2),
        settlement_id=settlement,
    )


def ambiguity(*, tolerance: int = 0) -> Ambiguity:
    """Two combinations, both summing to exactly 800000 paise."""
    return Ambiguity(
        credit_utr="UTR-77291",
        credit_paise=785_360,
        narration="NEFT RAZORPAYSETL88 CR",
        candidates=(
            Candidate("A", (leg("ORD-101", 300_000), leg("ORD-102", 500_000))),
            Candidate(
                "B",
                (
                    leg("ORD-115", 250_000, settlement="SETL-91"),
                    leg("ORD-118", 550_000, settlement="SETL-93"),
                ),
            ),
        ),
        target_paise=800_000,
        tolerance_paise=tolerance,
        credit_value_date=date(2026, 8, 4),
        inferred_fee_rate=0.0183,
    )


def verdict(**over: object) -> Verdict:
    base: dict[str, object] = dict(
        selected="A",
        confidence=0.97,
        reason=GOOD_REASON,
        evidence_fields=("narration", "settlement_id"),
    )
    base.update(over)
    return Verdict(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1 — hallucinated candidate
# ---------------------------------------------------------------------------


def test_a_good_verdict_passes_untouched() -> None:
    checked = verify(verdict(), ambiguity())
    assert checked.is_ok()
    assert checked.unwrap().confidence == pytest.approx(0.97)


def test_guardrail_catches_hallucination() -> None:
    """§4.4's own test: it answered 'Z' to a two-candidate question."""
    checked = verify(verdict(selected="Z"), ambiguity())
    assert checked.is_err()
    assert checked.unwrap_err() == HALLUCINATED_CANDIDATE


@given(
    st.text(min_size=0, max_size=8).filter(
        lambda s: s not in {"A", "B", NO_SELECTION}
    )
)
def test_no_string_outside_the_candidate_ids_is_ever_accepted(selected: str) -> None:
    """Exhaustive rather than sampled: the id is the only thing standing
    between a model's imagination and a journal entry.

    `NONE` is excluded because it is a deliberate third answer, not a candidate
    id — hypothesis found it here before the filter did, which is the property
    test earning its place.
    """
    assert verify(verdict(selected=selected), ambiguity()).is_err()


def test_the_right_orders_under_the_wrong_id_are_still_a_hallucination() -> None:
    """Restating candidate A's contents under a label that was never offered is
    the plausible-looking version of this failure, and it fails the same way."""
    assert verify(verdict(selected="ORD-101+ORD-102"), ambiguity()).is_err()


# ---------------------------------------------------------------------------
# 2 — arithmetic mismatch
# ---------------------------------------------------------------------------


def test_guardrail_catches_bad_arithmetic() -> None:
    """A candidate whose legs do not reach the target is not a solution,
    however confidently it is selected."""
    amb = Ambiguity(
        credit_utr="UTR-1",
        credit_paise=785_360,
        narration="NEFT",
        candidates=(
            Candidate("A", (leg("ORD-101", 300_000), leg("ORD-102", 500_000))),
            Candidate("B_wrong_sum", (leg("ORD-115", 250_000),)),
        ),
        target_paise=800_000,
    )
    checked = verify(verdict(selected="B_wrong_sum"), amb)
    assert checked.is_err()
    assert checked.unwrap_err() == ARITHMETIC_MISMATCH


def test_the_sum_is_recomputed_from_the_legs_not_taken_on_trust() -> None:
    """`Candidate.gross_paise` is derived on every access. A candidate whose
    rows were mutated after it was built cannot keep an inherited total."""
    amb = ambiguity()
    good = amb.by_id("A")
    assert good is not None
    assert good.gross_paise == sum(each.gross_equivalent_paise for each in good.legs)


def test_a_refund_leg_reduces_the_total_it_belongs_to() -> None:
    """Signed legs, so the guardrail arithmetic matches L3's own: a refund is a
    negative gross-equivalent and one sum compares to one target."""
    amb = Ambiguity(
        credit_utr="UTR-2",
        credit_paise=550_000,
        narration="NEFT",
        candidates=(
            Candidate("A", (leg("ORD-1", 800_000), leg("RFND-1", -240_000))),
            Candidate("B", (leg("ORD-2", 560_000),)),
            # The same rows with the refund entered as an inflow — the mistake
            # that would otherwise pass as "the amounts are all there".
            Candidate("C", (leg("ORD-1", 800_000), leg("RFND-1", 240_000))),
        ),
        target_paise=560_000,
    )
    assert verify(verdict(selected="A"), amb).is_ok()
    assert verify(verdict(selected="B"), amb).is_ok()
    assert verify(verdict(selected="C"), amb).unwrap_err() == ARITHMETIC_MISMATCH


def test_tolerance_is_honoured_so_the_guardrail_agrees_with_the_matcher() -> None:
    """L3 accepts a solution inside the rounding tolerance. A guardrail
    demanding exact equality would reject verdicts on candidates L3 itself
    considered valid, and the two layers would disagree about what a solution
    is."""
    amb = Ambiguity(
        credit_utr="UTR-3",
        credit_paise=785_360,
        narration="NEFT",
        candidates=(
            Candidate("A", (leg("ORD-1", 799_970),)),
            Candidate("B", (leg("ORD-2", 799_000),)),
        ),
        target_paise=800_000,
        tolerance_paise=50,
    )
    assert verify(verdict(selected="A"), amb).is_ok()  # 30 paise adrift, allowed
    assert verify(verdict(selected="B"), amb).is_err()  # 1000 paise, refused


# ---------------------------------------------------------------------------
# 3 — missing reason
# ---------------------------------------------------------------------------


def test_guardrail_catches_a_missing_reason() -> None:
    checked = verify(verdict(reason=""), ambiguity())
    assert checked.is_err()
    assert checked.unwrap_err() == MISSING_REASON


@pytest.mark.parametrize("reason", ["", "   ", "\n\t ", "A", "yes", "correct"])
def test_a_reason_nobody_can_read_is_no_reason(reason: str) -> None:
    """§2.7 rule 4: no automated decision without a justification. A one-word
    answer on an audit trail is a decision nobody can agree or disagree with."""
    assert verify(verdict(reason=reason), ambiguity()).is_err()


# ---------------------------------------------------------------------------
# Unsupported verdicts are halved, not rejected (§4.4)
# ---------------------------------------------------------------------------


def test_a_verdict_citing_no_evidence_is_halved_not_refused() -> None:
    """It may still be right — it just cannot be leaned on."""
    checked = verify(verdict(confidence=0.90, evidence_fields=()), ambiguity())
    assert checked.is_ok()
    assert checked.unwrap().confidence == pytest.approx(0.45)


def test_halving_never_leaves_the_valid_range() -> None:
    checked = verify(verdict(confidence=1.0, evidence_fields=()), ambiguity())
    assert 0.0 <= checked.unwrap().confidence <= 1.0


# ---------------------------------------------------------------------------
# Order of checks: the most dangerous failure is caught first
# ---------------------------------------------------------------------------


def test_a_hallucination_is_reported_as_a_hallucination_not_as_a_bad_reason() -> None:
    """A verdict can fail several checks at once. The reported code must name
    the worst one, because it is what a controller reads on the card."""
    checked = verify(verdict(selected="Z", reason=""), ambiguity())
    assert checked.unwrap_err() == HALLUCINATED_CANDIDATE


# ---------------------------------------------------------------------------
# Never raises: a bad verdict is data, not a bug (§5.5)
# ---------------------------------------------------------------------------


@given(
    selected=st.text(max_size=20),
    confidence=st.floats(min_value=0.0, max_value=1.0),
    reason=st.text(max_size=60),
    evidence=st.lists(st.text(max_size=12), max_size=4),
)
def test_verify_never_raises_on_any_well_typed_verdict(
    selected: str, confidence: float, reason: str, evidence: list[str]
) -> None:
    result = verify(
        Verdict(
            selected=selected,
            confidence=confidence,
            reason=reason,
            evidence_fields=tuple(evidence),
        ),
        ambiguity(),
    )
    assert result.is_ok() or result.is_err()


# ---------------------------------------------------------------------------
# Job B: the menu is the guardrail
# ---------------------------------------------------------------------------


def case() -> Unexplained:
    return Unexplained(
        ref="UTR-4482",
        amount_paise=420_000,
        narration="NEFT RAZORPAY CR",
        expected_gross_paise=428_000,
        nearest_rows=(leg("ORD-3312", 428_000, settlement=None),),
        allowed_classifications=("AMOUNT_MISMATCH", "FX_OR_SLAB_VARIANCE"),
        allowed_actions=("RAISE_GATEWAY_TICKET", "ESCALATE"),
        value_date=date(2026, 8, 4),
    )


def hypothesis(**over: object) -> Hypothesis:
    base: dict[str, object] = dict(
        classification="FX_OR_SLAB_VARIANCE",
        hypothesis="The ₹80 gap is consistent with an international MDR slab.",
        suggested_action="RAISE_GATEWAY_TICKET",
        confidence=0.55,
    )
    base.update(over)
    return Hypothesis(**base)  # type: ignore[arg-type]


def test_a_good_hypothesis_passes() -> None:
    assert verify_hypothesis(hypothesis(), case()).is_ok()


def test_an_invented_reason_code_is_refused() -> None:
    """`FX_VARIANCE` is not `FX_OR_SLAB_VARIANCE`. A card carrying a code the
    registry does not know renders with no behaviour behind it."""
    checked = verify_hypothesis(hypothesis(classification="FX_VARIANCE"), case())
    assert checked.unwrap_err() == UNKNOWN_CLASSIFICATION


def test_an_action_with_no_button_behind_it_is_refused() -> None:
    """A controller clicking a dead button is worse than reading plainer prose."""
    checked = verify_hypothesis(hypothesis(suggested_action="REFUND_IT"), case())
    assert checked.unwrap_err() == UNKNOWN_ACTION


def test_an_empty_hypothesis_is_refused() -> None:
    checked = verify_hypothesis(hypothesis(hypothesis=" "), case())
    assert checked.unwrap_err() == MISSING_HYPOTHESIS


def test_a_case_offering_no_menu_is_a_bug_not_a_verdict() -> None:
    """An empty menu would reject every answer silently, which reads in the
    metrics as a model that is always wrong."""
    with pytest.raises(ValueError, match="classifications"):
        Unexplained(
            ref="X",
            amount_paise=1,
            narration="",
            expected_gross_paise=1,
            nearest_rows=(),
            allowed_classifications=(),
            allowed_actions=("ESCALATE",),
        )


# ---------------------------------------------------------------------------
# Rejection codes are stable strings — they reach a controller
# ---------------------------------------------------------------------------


def test_every_rejection_code_is_a_lowercase_slug() -> None:
    codes = [
        guardrails.HALLUCINATED_CANDIDATE,
        guardrails.ARITHMETIC_MISMATCH,
        guardrails.MISSING_REASON,
        guardrails.UNKNOWN_CLASSIFICATION,
        guardrails.UNKNOWN_ACTION,
        guardrails.MISSING_HYPOTHESIS,
    ]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert code == code.lower().strip()
        assert " " not in code


# ---------------------------------------------------------------------------
# Abstention: "none of these" is an answer (§4.4 as applied)
# ---------------------------------------------------------------------------


def test_declining_to_choose_is_a_valid_verdict() -> None:
    """On seed 42 every candidate the solver offers is wrong — the true
    combination reconstructs 53 paise below target against a 50-paise
    tolerance, so it is excluded before the model is ever asked. Forcing a
    choice there manufactures a wrong match at high confidence."""
    checked = verify(
        verdict(
            selected=NO_SELECTION,
            reason=(
                "No candidate's settlement_ids match the batch named in the "
                "narration, and the capture dates are equally scattered."
            ),
        ),
        ambiguity(),
    )
    assert checked.is_ok()
    assert checked.unwrap().is_abstention


def test_an_abstention_still_has_to_justify_itself() -> None:
    checked = verify(verdict(selected=NO_SELECTION, reason="no"), ambiguity())
    assert checked.unwrap_err() == MISSING_REASON


def test_an_abstention_is_not_treated_as_a_hallucinated_candidate() -> None:
    """`NONE` is not a candidate id, and must not be reported as an invented
    one — the two mean opposite things on an exception card."""
    checked = verify(
        verdict(selected=NO_SELECTION, reason=GOOD_REASON), ambiguity()
    )
    assert checked.is_ok()


def test_an_abstention_skips_the_arithmetic_check() -> None:
    """There is no chosen combination to re-add. An abstention is the one
    verdict that cannot put a wrong number in the books."""
    amb = Ambiguity(
        credit_utr="UTR-9",
        credit_paise=1,
        narration="NEFT",
        candidates=(
            Candidate("A", (leg("ORD-1", 1),)),
            Candidate("B", (leg("ORD-2", 2),)),
        ),
        target_paise=999_999,
    )
    assert verify(verdict(selected="A", reason=GOOD_REASON), amb).is_err()
    assert verify(verdict(selected=NO_SELECTION, reason=GOOD_REASON), amb).is_ok()
