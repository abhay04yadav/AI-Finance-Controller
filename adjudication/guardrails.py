"""Verdict verification. Guide §4.4. Gate 11.

    LLM SELECTS. CODE VERIFIES. ALWAYS.

Three checks on job A, in the order §4.4 lists them:

  1. hallucinated_candidate — `selected` is not among the candidate ids
  2. arithmetic_mismatch    — the chosen candidate is not actually a solution
  3. missing_reason         — no human-readable justification

An **abstention** — the adjudicator answering "none of these" — passes checks 1
and 2 by construction: there is no chosen combination to look up or re-add. It
still has to justify itself, and it produces no match. Declining is the correct
answer when the true combination is not among the candidates at all, which on
seed 42 is exactly the situation (see `core.adjudication.NO_SELECTION`).

A rejected verdict does NOT retry blindly. The caller lets it fall through to an
exception with reason ADJUDICATION_REJECTED, which is itself an honest line for
the exception list. Retrying a failed verdict is how you talk a model into an
answer it already had reason not to give, and it costs money to do it.

Two deliberate departures from the guide's sketch, both worth defending:

**`fee: FeeModel` is not a parameter.** §4.4 writes the arithmetic check as
`fee.expected_net(chosen.gross_paise) != ambiguity.credit.amount.paise`. That
would require `adjudication/` to import `matching.fee_model`, which §3.2 forbids
— and re-running the fee model here would check the LLM against the same
arithmetic that produced the candidates, which is not an independent check at
all. Instead the comparison is between two integers computed before the model
was called: the target L3 back-solved, and the sum of the chosen candidate's own
legs. `Candidate.gross_paise` is recomputed from the legs on every access, so a
candidate whose stated total has drifted from its rows fails here.

**Tolerance is honoured.** L3 will accept a solution inside
`rounding_tolerance_paise`; a guardrail demanding exact equality would reject
verdicts on candidates L3 itself considered valid. The check uses the same
tolerance the ambiguity was built with, so guardrail and matcher agree on what
"a solution" means.

Job B gets its own verification (`verify_hypothesis`), because a free-text
hypothesis with an invented reason code or an action that has no button behind
it is exactly as wrong as a hallucinated candidate — it just fails later, on
someone's screen.
"""

from __future__ import annotations

from dataclasses import replace

from core.adjudication import (
    Ambiguity,
    Hypothesis,
    Unexplained,
    Verdict,
)
from core.result import Err, Ok, Result

#: Rejection codes. They reach a controller as the `detail` on an
#: ADJUDICATION_REJECTED card, so they are stable strings, not prose.
HALLUCINATED_CANDIDATE = "hallucinated_candidate"
ARITHMETIC_MISMATCH = "arithmetic_mismatch"
MISSING_REASON = "missing_reason"
UNKNOWN_CLASSIFICATION = "unknown_classification"
UNKNOWN_ACTION = "unknown_action"
MISSING_HYPOTHESIS = "missing_hypothesis"

#: A verdict that cites no evidence field is not wrong, but it is unsupported:
#: the model reached the right answer without saying what it read. §4.4 halves
#: its confidence rather than rejecting it, which is the honest treatment — the
#: answer may still be right, it just cannot be leaned on.
UNSUPPORTED_PENALTY = 0.5

#: The shortest reason we will accept. One word ("A", "yes", "correct") carries
#: no information onto an audit trail, and a reason nobody can read fails §2.7
#: rule 4 just as completely as no reason at all.
MIN_REASON_CHARS = 12


def verify(verdict: Verdict, ambiguity: Ambiguity) -> Result[Verdict, str]:
    """Check one job A verdict against the question it answered.

    Returns `Ok(verdict)` — possibly with a reduced confidence — or `Err(code)`.
    Never raises: a bad verdict is data, not a bug (§5.5).
    """
    if verdict.is_abstention:
        # "None of these" is an answer. It skips the arithmetic check because
        # there is no chosen combination to re-add, and it is the one verdict
        # that cannot put a wrong number in the books. Its reason is the whole
        # of it, so that check still applies.
        if len(verdict.reason.strip()) < MIN_REASON_CHARS:
            return Err(MISSING_REASON)
        return Ok(verdict)

    chosen = ambiguity.by_id(verdict.selected)
    if chosen is None:
        # It invented an option. This is the check that fires when a model
        # answers "C" to a two-candidate question, or restates the orders in a
        # combination that was never offered.
        return Err(HALLUCINATED_CANDIDATE)

    drift = abs(chosen.gross_paise - ambiguity.target_paise)
    if drift > ambiguity.tolerance_paise:
        # It picked a non-solution. Unreachable on candidates the solver
        # produced, and that is the point: this check is what stands between a
        # corrupted, mutated or hand-built candidate list and the books.
        return Err(ARITHMETIC_MISMATCH)

    if len(verdict.reason.strip()) < MIN_REASON_CHARS:
        return Err(MISSING_REASON)

    if not verdict.evidence_fields:
        return Ok(replace(verdict, confidence=verdict.confidence * UNSUPPORTED_PENALTY))

    return Ok(verdict)


def verify_hypothesis(
    hypothesis: Hypothesis, case: Unexplained
) -> Result[Hypothesis, str]:
    """Check one job B answer against the menu it was offered.

    The classification becomes a reason code on an exception card and the
    suggested action becomes a button. Both must exist. A model that returns
    `FX_VARIANCE` where the registry says `FX_OR_SLAB_VARIANCE` has produced
    something that renders as a card with no behaviour behind it, and a
    controller clicking a dead button is worse than a controller reading a
    plainer sentence.
    """
    if hypothesis.classification not in case.allowed_classifications:
        return Err(UNKNOWN_CLASSIFICATION)
    if hypothesis.suggested_action not in case.allowed_actions:
        return Err(UNKNOWN_ACTION)
    if len(hypothesis.hypothesis.strip()) < MIN_REASON_CHARS:
        return Err(MISSING_HYPOTHESIS)
    return Ok(hypothesis)
