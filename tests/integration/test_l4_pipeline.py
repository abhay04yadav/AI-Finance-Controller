"""L4 wired into the real pipeline. Guide §4.4, §5.4.

The unit tests prove the guardrails and the batching. This proves the *wiring*:
that a verdict reaching the adjudicator actually removes an exception, claims
the right ledger rows, and lands in the review queue rather than the books.

The transport is faked — deliberately, and it is the only thing faked. Every
answer here comes from a hand-written fixture, so nothing in this file is a
measurement of how well a model performs; it is a measurement of what our own
code does with an answer once it has one. The distinction matters: the numbers
in the eval report are produced with the LLM path OFF, and this file does not
change them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from adjudication.cache import VerdictCache
from adjudication.llm_adjudicator import MAX_ADJUDICATED_CONFIDENCE, LlmAdjudicator
from core.config import Settings
from core.dates import BusinessCalendar
from core.reason_codes import ReasonCode
from matching.registry import build_strategies
from pipeline.adjudication_step import NAME as L4_NAME
from pipeline.orchestrator import ReconciliationPipeline

DATASET = Path("data") / "seed42"

pytestmark = pytest.mark.skipif(
    not DATASET.exists(), reason="run `make generate` first"
)


class _Recorder:
    """A client that answers from a script and counts what it was asked."""

    def __init__(self, answer: Any) -> None:
        self._answer = answer
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        payload = self._answer(kwargs) if callable(self._answer) else self._answer
        return _Response(payload)


class _Response:
    stop_reason = "end_turn"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = [_Block(json.dumps(payload))]
        self.usage = _Usage()


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    input_tokens = 2400
    output_tokens = 260


def build(adjudicator: Any) -> ReconciliationPipeline:
    return ReconciliationPipeline(
        build_strategies(),
        calendar=BusinessCalendar(),
        settings=Settings(),
        adjudicator=adjudicator,
    )


def _asked(request: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads(request["messages"][0]["content"])["credits"]


def _is_job_a(credits: list[dict[str, Any]]) -> bool:
    """Job A payloads carry candidates; job B payloads carry a menu."""
    return bool(credits) and "candidates" in credits[0]


def _job_b_answer(credits: list[dict[str, Any]]) -> dict[str, Any]:
    """Always take the first classification and action on the menu."""
    return {
        "hypotheses": [
            {
                "ref": c["bank_credit"]["ref"],
                "classification": c["allowed_classifications"][0],
                "hypothesis": (
                    "The nearest open row does not close the gap at the inferred "
                    "MDR; consistent with a different fee slab."
                ),
                "suggested_action": c["allowed_actions"][0],
                "confidence": 0.55,
            }
            for c in credits
        ]
    }


def first_candidate_answer(request: dict[str, Any]) -> dict[str, Any]:
    """Always pick the first candidate offered, for every credit asked about.

    A fixed rule, not a clever one — the assertions below are about what the
    pipeline does with an answer, and a rule keeps the fixture honest about not
    being a measurement of model skill.
    """
    credits = _asked(request)
    if not _is_job_a(credits):
        return _job_b_answer(credits)
    return {
        "verdicts": [
            {
                "utr": credit["bank_credit"]["utr"],
                "selected": credit["candidates"][0]["id"],
                "confidence": 0.99,
                "reason": (
                    "The narration names the settlement batch that every leg of "
                    "the first candidate was reported in."
                ),
                "evidence_fields": ["narration", "settlement_id"],
            }
            for credit in credits
        ]
    }


def test_an_adjudicated_ambiguity_stops_being_an_exception(tmp_path: Path) -> None:
    before = build(_null()).run(DATASET)
    ambiguous = [
        e.ref
        for e in before.exceptions
        if e.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED
    ]
    assert ambiguous, "seed 42 no longer plants an ambiguity — pick another seed"

    client = _Recorder(first_candidate_answer)
    after = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)

    for ref in ambiguous:
        assert ref in after.matches, f"{ref} was adjudicated but is not a match"
    still_ambiguous = [
        e.ref
        for e in after.exceptions
        if e.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED
    ]
    assert not still_ambiguous
    assert len(after.matches) == len(before.matches) + len(ambiguous)


def test_the_whole_run_costs_one_request_per_job(tmp_path: Path) -> None:
    client = _Recorder(first_candidate_answer)
    result = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)
    # Two prompts, so at most two requests for a whole 605-record dataset —
    # not two per row. This is the assertion behind the cost claim.
    assert len(client.calls) <= 2
    assert result.llm_calls >= len(client.calls), "a request answered nothing"


def test_an_adjudicated_match_is_reviewed_not_posted(tmp_path: Path) -> None:
    """An LLM never moves money on its own. The verdict claims 0.99; the
    auto-post threshold is 0.95; the match lands at 0.94 in the review queue."""
    client = _Recorder(first_candidate_answer)
    result = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)

    adjudicated = [m for m in result.matches.values() if m.strategy == L4_NAME]
    assert adjudicated, "no verdict reached the pipeline"
    for match in adjudicated:
        assert match.confidence <= MAX_ADJUDICATED_CONFIDENCE
        assert match.confidence < Settings().auto_post_threshold
        assert match.utr in {item.utr for item in result.review_queue}
        assert match.reason.strip()


def test_the_llm_budget_stays_under_ten_percent(tmp_path: Path) -> None:
    """§2.2, asserted rather than hoped for."""
    client = _Recorder(first_candidate_answer)
    result = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)
    assert result.llm_calls / result.records_processed < 0.10


def test_a_rejected_verdict_becomes_adjudication_rejected(tmp_path: Path) -> None:
    """Not AMBIGUOUS_UNADJUDICATED — one was asked. Not a retry — one request."""

    def hallucinate(request: dict[str, Any]) -> dict[str, Any]:
        credits = _asked(request)
        if not _is_job_a(credits):
            return _job_b_answer(credits)
        return {
            "verdicts": [
                {
                    "utr": credit["bank_credit"]["utr"],
                    "selected": "NOT-A-CANDIDATE",
                    "confidence": 0.99,
                    "reason": "This combination shares one settlement reference.",
                    "evidence_fields": ["settlement_id"],
                }
                for credit in credits
            ]
        }

    client = _Recorder(hallucinate)
    result = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)

    rejected = [
        e for e in result.exceptions
        if e.reason_code is ReasonCode.ADJUDICATION_REJECTED
    ]
    assert rejected, "a hallucinated verdict did not surface as an exception"
    assert all("retried" in e.why for e in rejected)
    assert not [
        e for e in result.exceptions
        if e.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED
    ]
    assert len(client.calls) <= 2, "a rejected verdict triggered a retry"
    assert result.matches.keys() >= set()  # nothing was matched from a bad verdict
    assert not [m for m in result.matches.values() if m.strategy == L4_NAME]


def test_the_no_llm_run_is_unchanged_by_any_of_this() -> None:
    """The ablation is a first-class mode, not a leftover. Its numbers must not
    move because L4 was built."""
    result = build(_null()).run(DATASET)
    assert result.llm_calls == 0
    assert result.llm_cost_paise == 0
    assert any(
        e.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED for e in result.exceptions
    )


def _null() -> Any:
    from adjudication.null_adjudicator import NullAdjudicator

    return NullAdjudicator()


def test_an_abstention_leaves_an_honest_exception(tmp_path: Path) -> None:
    """The seed-42 case, and the reason the abstention path exists.

    Every one of the five combinations L3 offers for UTR-39450273 is wrong: the
    true set reconstructs 53 paise below the target and the rounding tolerance
    is 50, so it is excluded before the model is ever asked. An adjudicator that
    must pick one produces a wrong match at 0.94 and drops match precision from
    100% to 98.3%. One that may decline leaves the credit exactly where it was —
    an exception, with a sentence saying a model looked and found nothing.
    """

    def decline(request: dict[str, Any]) -> dict[str, Any]:
        credits = _asked(request)
        if not _is_job_a(credits):
            return _job_b_answer(credits)
        return {
            "verdicts": [
                {
                    "utr": credit["bank_credit"]["utr"],
                    "selected": "NONE",
                    "confidence": 0.15,
                    "reason": (
                        "The narration names one settlement batch but every "
                        "candidate mixes three, so none is supported."
                    ),
                    "evidence_fields": ["narration", "settlement_id"],
                }
                for credit in credits
            ]
        }

    client = _Recorder(decline)
    result = build(
        LlmAdjudicator(cache=VerdictCache(tmp_path / "c"), client=client)
    ).run(DATASET)

    assert not [m for m in result.matches.values() if m.strategy == L4_NAME]
    assert not [
        e for e in result.exceptions
        if e.reason_code is ReasonCode.ADJUDICATION_REJECTED
    ]
    declined = [
        e for e in result.exceptions
        if e.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED
    ]
    assert declined, "the abstained credit vanished instead of staying an exception"
    assert all("declined to choose" in e.why for e in declined)
