"""Response contracts for structured LLM output. Guide §4.4, §9.7.

Two layers of contract, and they do different jobs:

  * The **pydantic models** below are what the wire format must satisfy. They
    are tested against recorded fixtures in `tests/fixtures/adjudication/`, so
    the contract test runs offline, with no API key and no network — §9.7's
    "tested against recorded fixtures so it runs offline".
  * The **JSON schema** derived from them is sent to the API as
    `output_config.format`, so the model is constrained at generation time
    rather than corrected afterwards.

`extra="forbid"` is what makes the derived schema emit
`additionalProperties: false`, which the API requires for a strict json_schema
format. It also means a response carrying a field we never asked for is a
parse failure rather than a silently ignored surprise.

Nothing here is trusted. A response can satisfy every constraint in this file
and still be wrong — `guardrails.verify` is what decides that. This layer only
guarantees the answer has the right *shape*.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.adjudication import Hypothesis, Verdict


class VerdictResponse(BaseModel):
    """Job A: one selection among the candidates that were offered."""

    model_config = ConfigDict(extra="forbid")

    selected: str = Field(description="The id of the chosen candidate, exactly as given.")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(
        description="Why this candidate and not the others, in one or two sentences "
        "a finance controller can act on."
    )
    evidence_fields: list[str] = Field(
        default_factory=list,
        description="Which supplied fields the decision rested on, e.g. "
        "narration, settlement_id, capture_date.",
    )

    def to_verdict(self, *, prompt_version: str, model: str, origin: str) -> Verdict:
        return Verdict(
            selected=self.selected,
            confidence=self.confidence,
            reason=self.reason,
            evidence_fields=tuple(self.evidence_fields),
            prompt_version=prompt_version,
            model=model,
            origin=origin,
        )


class HypothesisResponse(BaseModel):
    """Job B: a classification, a hypothesis and something to do about it."""

    model_config = ConfigDict(extra="forbid")

    classification: str = Field(
        description="One of the allowed_classifications, copied exactly."
    )
    hypothesis: str = Field(
        description="What most likely happened, naming the specific figures. "
        "This becomes the WHY on the exception card."
    )
    suggested_action: str = Field(
        description="One of the allowed_actions, copied exactly."
    )
    confidence: float = Field(ge=0.0, le=1.0)

    def to_hypothesis(
        self, *, prompt_version: str, model: str, origin: str
    ) -> Hypothesis:
        return Hypothesis(
            classification=self.classification,
            hypothesis=self.hypothesis,
            suggested_action=self.suggested_action,
            confidence=self.confidence,
            prompt_version=prompt_version,
            model=model,
            origin=origin,
        )


class BatchVerdictResponse(BaseModel):
    """Every job A answer in one response.

    Batched because §4.4 is explicit about it: one call per row is where the
    ₹40-vs-₹0.31 cost gap comes from. `utr` keys the answer back to its
    question — results are matched by key, never by position, because a model
    that returns three answers for four questions would otherwise silently
    shift every verdict onto the wrong credit.
    """

    model_config = ConfigDict(extra="forbid")

    verdicts: list[KeyedVerdict] = Field(default_factory=list)


class KeyedVerdict(VerdictResponse):
    model_config = ConfigDict(extra="forbid")

    utr: str


class BatchHypothesisResponse(BaseModel):
    """Every job B answer in one response, keyed by the case reference."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[KeyedHypothesis] = Field(default_factory=list)


class KeyedHypothesis(HypothesisResponse):
    model_config = ConfigDict(extra="forbid")

    ref: str


BatchVerdictResponse.model_rebuild()
BatchHypothesisResponse.model_rebuild()


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The schema in the shape `output_config.format` wants.

    Pydantic emits `$defs`/`$ref` for nested models, which the API accepts, and
    `extra="forbid"` has already put `additionalProperties: false` on every
    object. Required-ness comes from the fields having no defaults.
    """
    return model.model_json_schema()
