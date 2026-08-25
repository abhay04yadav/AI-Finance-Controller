"""L4 as a whole: batching, budget, cache, and what happens when it is wrong.
Guide §4.4, §9.7. Review Guide gate 11.

Every test runs offline against RECORDED FIXTURES in
`tests/fixtures/adjudication/`. The transport is the only thing not exercised
here, and it is the only thing that cannot change our numbers without also
changing a cache key.

The fake client counts requests, which is what makes "batched, never one call
per row" an assertion rather than a claim in a README.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from adjudication.cache import VerdictCache
from adjudication.guardrails import HALLUCINATED_CANDIDATE
from adjudication.llm_adjudicator import (
    MAX_ADJUDICATED_CONFIDENCE,
    MODEL,
    LlmAdjudicator,
)
from adjudication.null_adjudicator import NullAdjudicator
from core.adjudication import (
    NO_SELECTION,
    Ambiguity,
    Candidate,
    CandidateLeg,
    Unexplained,
)
from core.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "adjudication"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A fake transport that records exactly what it was asked
# ---------------------------------------------------------------------------


class FakeUsage:
    input_tokens = 1200
    output_tokens = 180


class FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = [FakeBlock(json.dumps(payload))]
        self.usage = FakeUsage()


class FakeMessages:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self._payloads.pop(0))


class FakeClient:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self.messages = FakeMessages(list(payloads))

    @property
    def requests(self) -> int:
        return len(self.messages.calls)


# ---------------------------------------------------------------------------
# Fixtures for the domain objects
# ---------------------------------------------------------------------------


def leg(order_id: str, paise: int, settlement: str | None = "SETL-88") -> CandidateLeg:
    return CandidateLeg(order_id, paise, date(2026, 8, 2), settlement)


def ambiguity(utr: str = "UTR-77291") -> Ambiguity:
    return Ambiguity(
        credit_utr=utr,
        credit_paise=785_360,
        narration="NEFT RAZORPAYSETL88 CR",
        candidates=(
            Candidate("A", (leg("ORD-101", 300_000), leg("ORD-102", 500_000))),
            Candidate("B", (leg("ORD-115", 800_000, "SETL-91"),)),
        ),
        target_paise=800_000,
        credit_value_date=date(2026, 8, 4),
        inferred_fee_rate=0.0183,
    )


def case(ref: str = "UTR-4482") -> Unexplained:
    return Unexplained(
        ref=ref,
        amount_paise=420_000,
        narration="NEFT RAZORPAY CR",
        expected_gross_paise=428_000,
        nearest_rows=(leg("ORD-3312", 428_000, None),),
        allowed_classifications=("AMOUNT_MISMATCH", "FX_OR_SLAB_VARIANCE"),
        allowed_actions=("RAISE_GATEWAY_TICKET", "ESCALATE"),
        value_date=date(2026, 8, 4),
    )


@pytest.fixture
def cache(tmp_path: Path) -> VerdictCache:
    return VerdictCache(tmp_path / "cache")


# ---------------------------------------------------------------------------
# Job A
# ---------------------------------------------------------------------------


def test_a_verified_verdict_becomes_a_selection(cache: VerdictCache) -> None:
    client = FakeClient(fixture("job_a_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [], budget=10
    )
    assert result.verdicts["UTR-77291"].selected == "A"
    assert result.rejections == {}
    assert result.calls == 1
    assert result.api_requests == 1


def test_a_hallucinated_verdict_is_rejected_and_never_retried(
    cache: VerdictCache,
) -> None:
    """The Review Guide's question: does it retry? It does not. One request went
    out, the answer failed guardrail 1, and the credit stays an exception."""
    client = FakeClient(fixture("job_a_hallucination.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [], budget=10
    )
    assert result.verdicts == {}
    assert result.rejections["UTR-77291"] == HALLUCINATED_CANDIDATE
    assert client.requests == 1, "a rejected verdict was retried"


def test_an_llm_verdict_can_never_reach_the_auto_post_band(
    cache: VerdictCache,
) -> None:
    """The fixture claims 0.97 and the auto-post threshold is 0.95. A model
    choosing between two arithmetically identical answers prepares an entry for
    a human; it does not post one."""
    client = FakeClient(fixture("job_a_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [], budget=10
    )
    confidence = result.verdicts["UTR-77291"].confidence
    assert confidence <= MAX_ADJUDICATED_CONFIDENCE
    assert confidence < Settings().auto_post_threshold


def test_the_verdict_records_which_prompt_and_model_produced_it(
    cache: VerdictCache,
) -> None:
    """§4.4: version the prompt file and log the version with every verdict."""
    client = FakeClient(fixture("job_a_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [], budget=10
    )
    verdict = result.verdicts["UTR-77291"]
    assert verdict.model == MODEL
    assert verdict.prompt_version.startswith("job_a_select@")
    assert verdict.origin == "api"


# ---------------------------------------------------------------------------
# Batching — the ₹40-vs-₹0.31 question
# ---------------------------------------------------------------------------


def test_many_ambiguities_cost_one_request(cache: VerdictCache) -> None:
    ambiguities = [ambiguity(f"UTR-{i}") for i in range(12)]
    payload = {
        "verdicts": [
            {
                "utr": a.credit_utr,
                "selected": "A",
                "confidence": 0.8,
                "reason": "Narration names SETL-88, which candidate A matches.",
                "evidence_fields": ["narration"],
            }
            for a in ambiguities
        ]
    }
    client = FakeClient(payload)
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        ambiguities, [], budget=100
    )
    assert len(result.verdicts) == 12
    assert client.requests == 1, "one call per row — the expensive mistake"


def test_the_two_jobs_are_one_request_each(cache: VerdictCache) -> None:
    """Two prompts, so two requests — not two per row."""
    client = FakeClient(fixture("job_a_response.json"), fixture("job_b_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [case()], budget=10
    )
    assert client.requests == 2
    assert result.api_requests == 2


def test_answers_are_matched_by_key_never_by_position(cache: VerdictCache) -> None:
    """A model that answers three of four questions must not shift every
    verdict onto the wrong credit."""
    ambiguities = [ambiguity("UTR-1"), ambiguity("UTR-2"), ambiguity("UTR-3")]
    client = FakeClient(
        {
            "verdicts": [
                {
                    "utr": "UTR-3",
                    "selected": "B",
                    "confidence": 0.8,
                    "reason": "Candidate B is the single order in SETL-91.",
                    "evidence_fields": ["settlement_id"],
                }
            ]
        }
    )
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        ambiguities, [], budget=10
    )
    assert set(result.verdicts) == {"UTR-3"}
    assert result.verdicts["UTR-3"].selected == "B"
    assert any("UTR-1" in n for n in result.notes)


# ---------------------------------------------------------------------------
# The budget is enforced, not merely measured
# ---------------------------------------------------------------------------


def test_cases_past_the_budget_are_never_sent(cache: VerdictCache) -> None:
    ambiguities = [ambiguity(f"UTR-{i:02d}") for i in range(10)]
    payload = {
        "verdicts": [
            {
                "utr": a.credit_utr,
                "selected": "A",
                "confidence": 0.8,
                "reason": "Narration names SETL-88, which candidate A matches.",
                "evidence_fields": ["narration"],
            }
            for a in ambiguities[:3]
        ]
    }
    client = FakeClient(payload)
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        ambiguities, [], budget=3
    )
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert len(sent["credits"]) == 3
    assert len(result.skipped_over_budget) == 7
    assert any("budget" in n for n in result.notes)


def test_a_zero_budget_asks_nothing(cache: VerdictCache) -> None:
    client = FakeClient()
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [case()], budget=0
    )
    assert client.requests == 0
    assert result.calls == 0
    assert len(result.skipped_over_budget) == 2


def test_the_budget_picks_the_same_cases_every_time(cache: VerdictCache) -> None:
    """Sorted first, capped second. Capping an unsorted list would drop
    different credits on different runs and the metrics would wander."""
    ambiguities = [ambiguity(f"UTR-{i:02d}") for i in range(6)]
    payload = {"verdicts": []}

    def skipped(order: list[Ambiguity]) -> tuple[str, ...]:
        return (
            LlmAdjudicator(cache=cache, client=FakeClient(dict(payload)))
            .adjudicate(order, [], budget=2)
            .skipped_over_budget
        )

    assert skipped(ambiguities) == skipped(list(reversed(ambiguities)))


def test_job_a_is_funded_before_job_b(cache: VerdictCache) -> None:
    """A verdict can turn an exception into a match; a hypothesis only improves
    the prose on one that stays an exception."""
    client = FakeClient(fixture("job_a_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [case()], budget=1
    )
    assert "UTR-77291" in result.verdicts
    assert result.skipped_over_budget == ("UTR-4482",)


def test_the_budget_matches_the_ten_percent_rule() -> None:
    from pipeline.adjudication_step import budget_for

    assert budget_for(605, Settings()) == 60
    assert budget_for(500, Settings()) == 50
    assert budget_for(5, Settings()) == 0


# ---------------------------------------------------------------------------
# The cache is what makes two runs identical
# ---------------------------------------------------------------------------


def test_a_second_run_costs_nothing_and_answers_the_same(cache: VerdictCache) -> None:
    first = LlmAdjudicator(cache=cache, client=FakeClient(fixture("job_a_response.json")))
    a = first.adjudicate([ambiguity()], [], budget=10)

    # No payloads left: a request here raises IndexError rather than passing.
    second = LlmAdjudicator(cache=cache, client=FakeClient())
    b = second.adjudicate([ambiguity()], [], budget=10)

    assert b.verdicts["UTR-77291"].selected == a.verdicts["UTR-77291"].selected
    assert b.verdicts["UTR-77291"].confidence == a.verdicts["UTR-77291"].confidence
    assert b.api_requests == 0
    assert b.cost_paise == 0
    assert b.verdicts["UTR-77291"].origin == "cache"


def test_a_changed_question_does_not_reuse_the_old_answer(cache: VerdictCache) -> None:
    """The key covers the question. Change the candidates and the old verdict
    must not be served against the new ones."""
    LlmAdjudicator(cache=cache, client=FakeClient(fixture("job_a_response.json"))).adjudicate(
        [ambiguity()], [], budget=10
    )
    changed = Ambiguity(
        credit_utr="UTR-77291",
        credit_paise=785_360,
        narration="NEFT RAZORPAYSETL91 CR",  # one word different
        candidates=ambiguity().candidates,
        target_paise=800_000,
        credit_value_date=date(2026, 8, 4),
        inferred_fee_rate=0.0183,
    )
    with pytest.raises(IndexError):
        LlmAdjudicator(cache=cache, client=FakeClient()).adjudicate(
            [changed], [], budget=10
        )


def test_the_cache_key_is_stable_across_processes() -> None:
    """Hashed from sorted JSON, so it does not depend on dict ordering, PYTHONHASHSEED
    or the machine."""
    assert ambiguity().cache_key() == ambiguity().cache_key()
    assert ambiguity("UTR-1").cache_key() != ambiguity("UTR-2").cache_key()


def test_only_the_answer_is_cached_not_our_bookkeeping(cache: VerdictCache) -> None:
    LlmAdjudicator(cache=cache, client=FakeClient(fixture("job_a_response.json"))).adjudicate(
        [ambiguity()], [], budget=10
    )
    files = list(cache.directory.glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert "_origin" not in stored["answer"]
    assert stored["meta"]["model"] == MODEL


# ---------------------------------------------------------------------------
# Job B
# ---------------------------------------------------------------------------


def test_a_hypothesis_becomes_the_why_and_the_action(cache: VerdictCache) -> None:
    client = FakeClient(fixture("job_b_response.json"))
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [], [case()], budget=10
    )
    hypothesis = result.hypotheses["UTR-4482"]
    assert hypothesis.classification == "FX_OR_SLAB_VARIANCE"
    assert hypothesis.suggested_action == "RAISE_GATEWAY_TICKET"
    assert "MDR slab" in hypothesis.hypothesis


def test_a_hypothesis_off_the_menu_is_rejected(cache: VerdictCache) -> None:
    payload = fixture("job_b_response.json")
    payload["hypotheses"][0]["suggested_action"] = "REFUND_IT"
    client = FakeClient(payload)
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [], [case()], budget=10
    )
    assert result.hypotheses == {}
    assert result.hypothesis_rejections["UTR-4482"] == "unknown_action"


# ---------------------------------------------------------------------------
# What the request actually looks like
# ---------------------------------------------------------------------------


def test_the_request_constrains_the_model_to_our_schema(cache: VerdictCache) -> None:
    client = FakeClient(fixture("job_a_response.json"))
    LlmAdjudicator(cache=cache, client=client).adjudicate([ambiguity()], [], budget=10)
    sent = client.messages.calls[0]
    assert sent["model"] == MODEL
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "verdicts" in fmt["schema"]["properties"]


def test_no_temperature_is_sent(cache: VerdictCache) -> None:
    """§4.4 asks for temperature 0; Claude Opus 5 rejects the parameter with a
    400. Determinism comes from the cache instead — see adjudication/cache.py."""
    client = FakeClient(fixture("job_a_response.json"))
    LlmAdjudicator(cache=cache, client=client).adjudicate([ambiguity()], [], budget=10)
    assert "temperature" not in client.messages.calls[0]


def test_the_stable_prompt_is_cacheable_and_the_question_comes_last(
    cache: VerdictCache,
) -> None:
    """Prefix caching is a prefix match: the frozen instructions carry the
    breakpoint and the volatile question sits after it."""
    client = FakeClient(fixture("job_a_response.json"))
    LlmAdjudicator(cache=cache, client=client).adjudicate([ambiguity()], [], budget=10)
    sent = client.messages.calls[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1]["role"] == "user"


def test_a_refusal_leaves_the_case_on_the_exception_list(cache: VerdictCache) -> None:
    class Refusing(FakeClient):
        def __init__(self) -> None:
            super().__init__({"verdicts": []})
            original = self.messages.create

            def create(**kwargs: Any) -> FakeResponse:
                response = original(**kwargs)
                response.stop_reason = "refusal"
                return response

            self.messages.create = create  # type: ignore[method-assign]

    result = LlmAdjudicator(cache=cache, client=Refusing()).adjudicate(
        [ambiguity()], [], budget=10
    )
    assert result.verdicts == {}
    assert any("declined" in n for n in result.notes)


def test_a_truncated_response_is_not_partially_used(cache: VerdictCache) -> None:
    class Truncating(FakeClient):
        def __init__(self) -> None:
            super().__init__(fixture("job_a_response.json"))
            original = self.messages.create

            def create(**kwargs: Any) -> FakeResponse:
                response = original(**kwargs)
                response.stop_reason = "max_tokens"
                return response

            self.messages.create = create  # type: ignore[method-assign]

    result = LlmAdjudicator(cache=cache, client=Truncating()).adjudicate(
        [ambiguity()], [], budget=10
    )
    assert result.verdicts == {}
    assert any("max_tokens" in n for n in result.notes)


# ---------------------------------------------------------------------------
# The --no-llm path (§4.4)
# ---------------------------------------------------------------------------


def test_the_null_adjudicator_declines_everything_and_costs_nothing() -> None:
    result = NullAdjudicator().adjudicate([ambiguity()], [case()], budget=100)
    assert result.verdicts == {}
    assert result.rejections == {}
    assert result.calls == 0
    assert result.api_requests == 0
    assert result.cost_paise == 0
    assert result.notes


def test_the_null_adjudicator_does_not_reclassify_anything() -> None:
    """It leaves L3's AMBIGUOUS_UNADJUDICATED standing. Rewriting it as
    ADJUDICATION_REJECTED would make the exception page claim an AI tried and
    failed on a run where no AI existed."""
    result = NullAdjudicator().adjudicate([ambiguity()], [], budget=100)
    assert result.rejections == {}
    assert result.hypothesis_rejections == {}


def test_both_adjudicators_satisfy_the_same_protocol() -> None:
    """Liskov (§5.4): either can be swapped in without the caller changing.

    The protocol is deliberately NOT `@runtime_checkable` — that would only
    check method names and would call any object with an `adjudicate` attribute
    conformant. `mypy --strict` checks these two annotations properly; the
    assertions below are the runtime half.
    """
    from adjudication.protocols import Adjudicator

    null: Adjudicator = NullAdjudicator()
    live: Adjudicator = LlmAdjudicator()
    for adjudicator in (null, live):
        assert adjudicator.name.startswith("L4_")
        assert callable(adjudicator.adjudicate)


# ---------------------------------------------------------------------------
# No credential, no network: the system still runs
# ---------------------------------------------------------------------------


def test_without_a_client_nothing_is_guessed(cache: VerdictCache) -> None:
    result = LlmAdjudicator(cache=cache, allow_network=False).adjudicate(
        [ambiguity()], [case()], budget=10
    )
    assert result.verdicts == {}
    assert result.calls == 0
    assert any("no API credential" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Abstention: the answer that costs nothing to be right about
# ---------------------------------------------------------------------------


def test_an_abstention_produces_no_match_and_no_rejection(cache: VerdictCache) -> None:
    client = FakeClient(
        {
            "verdicts": [
                {
                    "utr": "UTR-77291",
                    "selected": NO_SELECTION,
                    "confidence": 0.2,
                    "reason": (
                        "Neither candidate's settlement_ids match the batch in "
                        "the narration and both spans of capture dates are "
                        "equally plausible."
                    ),
                    "evidence_fields": ["narration", "settlement_id"],
                }
            ]
        }
    )
    result = LlmAdjudicator(cache=cache, client=client).adjudicate(
        [ambiguity()], [], budget=10
    )
    assert result.verdicts == {}
    assert result.rejections == {}
    assert "UTR-77291" in result.abstentions
    assert "settlement_ids" in result.abstentions["UTR-77291"]
    # It answered, so it counts against the budget — declining is not free.
    assert result.calls == 1
