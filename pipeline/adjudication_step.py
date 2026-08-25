"""The L4 step: hand what arithmetic could not settle to the adjudicator, then
fold the answers back into the run. Guide §4.4.

This module exists so `orchestrator.py` keeps its promise to own no business
logic. It owns none either — every decision here is a lookup or a routing rule.
What it does own is the **join**: it is the only layer that may see both
`adjudication/` (which knows nothing about exception cards) and `exceptions_/`
(which knows nothing about models), which is exactly why the menu of allowed
classifications and actions is attached here and nowhere else.

Three routing rules, and all three are about honesty:

  * A verdict that passed the guardrails **replaces** L3's
    AMBIGUOUS_UNADJUDICATED flag with a match. The flag said "no adjudicator was
    asked"; one was.
  * A verdict that failed a guardrail **rewrites** the flag as
    ADJUDICATION_REJECTED, naming the check that caught it. No retry.
  * An **abstention** — the adjudicator looked at every combination and said
    none is supported — leaves the credit an exception and records that a model
    was asked and declined. On seed 42 this is the right answer: the true
    combination reconstructs 53 paise below target against a 50-paise tolerance,
    so it is not among the candidates at all and every option is wrong.
  * A job B hypothesis **enriches** the existing exception's WHY and adds the
    suggested action to the front of its button list. It never changes the
    reason code and never removes the deterministic explanation — the model's
    sentence is added to what the system already knew, not substituted for it.
"""

from __future__ import annotations

from dataclasses import replace

from adjudication.protocols import Adjudicator
from core.adjudication import AdjudicationResult
from core.config import Settings
from core.models import MatchProposal
from core.reason_codes import ReasonCode
from core.run_result import ActionOffer, ExceptionOutcome
from exceptions_.actions import available_for
from matching.protocols import Flag, MatchContext

NAME = "L4_adjudicate"

#: What job B may classify a stubbornly unexplained credit as. Not every reason
#: code: a credit that reached L4 is one the matcher could not place, so
#: "the money is still in transit" or "the row would not parse" are not on
#: offer — they describe things a different layer already decided.
JOB_B_CLASSIFICATIONS: tuple[ReasonCode, ...] = (
    ReasonCode.AMOUNT_MISMATCH,
    ReasonCode.FX_OR_SLAB_VARIANCE,
    ReasonCode.CROSS_PERIOD_REFUND,
    ReasonCode.MISSING_IN_LEDGER,
    ReasonCode.ROUNDING_DRIFT,
    ReasonCode.DUPLICATE_UTR,
)


def budget_for(records: int, settings: Settings) -> int:
    """How many cases may reach L4 at all (§2.2).

    Floored, not rounded: 10% of 605 records is 60.5 cases, and a system whose
    stated ceiling is "under 10%" should spend 60.
    """
    return int(records * settings.llm_budget_ratio)


def adjudicate(
    ctx: MatchContext,
    adjudicator: Adjudicator,
    *,
    records: int,
    settings: Settings,
) -> AdjudicationResult:
    """Ask L4, accept what survives, and leave the rest as it was."""
    cases = tuple(
        evidence.offering(
            classifications=tuple(str(c) for c in JOB_B_CLASSIFICATIONS),
            actions=_action_menu(),
        )
        for evidence in ctx.unexplained
    )
    result = adjudicator.adjudicate(
        tuple(ctx.ambiguities), cases, budget=budget_for(records, settings)
    )
    _accept_verdicts(ctx, result)
    return result


def _action_menu() -> tuple[str, ...]:
    """The action codes an unexplained credit can actually be given.

    Read from the registry rather than listed here, so an action added at gate
    13 becomes available to the model without anyone remembering to update a
    constant — and so the model can never suggest a button that does not exist.
    """
    probe = ExceptionOutcome(ref="probe", reason_code=ReasonCode.AMOUNT_MISMATCH)
    return tuple(offer.code for offer in available_for(probe))


def _accept_verdicts(ctx: MatchContext, result: AdjudicationResult) -> None:
    """Turn surviving verdicts into proposals and rewrite the flags they answer."""
    if not (result.verdicts or result.rejections or result.abstentions):
        return

    by_utr = {a.credit_utr: a for a in ctx.ambiguities}
    answered = set(result.verdicts) | set(result.rejections) | set(result.abstentions)

    for utr, verdict in result.verdicts.items():
        ambiguity = by_utr.get(utr)
        if ambiguity is None:
            continue
        chosen = ambiguity.by_id(verdict.selected)
        if chosen is None:  # pragma: no cover - guardrail 1 already refused this
            continue
        ctx.accept(
            MatchProposal(
                bank_utr=utr,
                ledger_ids=frozenset(chosen.order_ids),
                confidence=verdict.confidence,
                strategy=NAME,
                evidence=tuple(verdict.evidence_fields),
                reason=verdict.reason,
            )
        )

    ctx.flags[:] = [
        _rewritten(flag, result) if flag.ref in answered else flag
        for flag in ctx.flags
        if not (flag.ref in result.verdicts
                and flag.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED)
    ]


def _rewritten(flag: Flag, result: AdjudicationResult) -> Flag:
    """Say on the card what actually happened when the model was asked."""
    if flag.reason_code is not ReasonCode.AMBIGUOUS_UNADJUDICATED:
        return flag

    code = result.rejections.get(flag.ref)
    if code is not None:
        return replace(
            flag,
            reason_code=ReasonCode.ADJUDICATION_REJECTED,
            why=(
                f"An adjudicator chose between the combinations and the answer "
                f"failed the {code.replace('_', ' ')} check, so it was discarded "
                "rather than retried. The credit stays unmatched, which is the "
                "honest outcome — a verified wrong answer is worse than none."
            ),
            raised_by=NAME,
        )

    abstained = result.abstentions.get(flag.ref)
    if abstained is not None:
        # The code stays AMBIGUOUS_UNADJUDICATED: the credit is still ambiguous
        # and still unresolved. What changes is the sentence — a controller
        # reading this card should know a model looked and found the evidence
        # insufficient, which is a different situation from nobody having looked.
        return replace(
            flag,
            why=(
                f"An adjudicator examined every combination and declined to "
                f"choose: {abstained} Nothing was matched, and no entry was "
                "prepared."
            ),
            raised_by=NAME,
        )
    return flag


def apply_hypotheses(
    exceptions: tuple[ExceptionOutcome, ...], result: AdjudicationResult
) -> tuple[ExceptionOutcome, ...]:
    """Fold job B's answers onto the cards they explain (§8.2).

    Additive on purpose. The deterministic `why` states what the system
    measured; the hypothesis states what that measurement is consistent with.
    Replacing the first with the second would trade a fact for an opinion.
    """
    if not result.hypotheses:
        return exceptions

    out: list[ExceptionOutcome] = []
    for exc in exceptions:
        hypothesis = result.hypotheses.get(exc.ref)
        if hypothesis is None:
            out.append(exc)
            continue
        out.append(
            replace(
                exc,
                why=(
                    f"{exc.why} Adjudicator ({hypothesis.confidence:.0%} "
                    f"confidence): {hypothesis.hypothesis}"
                ).strip(),
                actions=_action_first(exc.actions, hypothesis.suggested_action),
            )
        )
    return tuple(out)


def _action_first(
    actions: tuple[ActionOffer, ...], suggested: str
) -> tuple[ActionOffer, ...]:
    """Move the suggested action to the front. It is a recommendation, not a
    decision: every other action stays on the card, in its original order."""
    chosen = [a for a in actions if a.code == suggested]
    return tuple(chosen + [a for a in actions if a.code != suggested])
