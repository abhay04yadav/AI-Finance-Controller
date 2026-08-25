"""The L4 contract: what an adjudicator is asked, and what it may answer.
Guide §4.4.

These types live in `core/` rather than in `adjudication/` for one reason worth
stating: §3.2 lets `adjudication/` import only `core/`, and the producer of an
ambiguity is `matching/`. Putting the contract in the middle is what lets L3
hand work to L4 without either package importing the other, and it is why every
guardrail test in this repo runs with no matcher, no dataset and no API key.

Two shapes, because §4.4 is two jobs:

  * `Ambiguity`   -> `Verdict`     job A: several combinations, pick one
  * `Unexplained` -> `Hypothesis`  job B: nothing adds up, say why in English

Everything the adjudicator needs to be CHECKED is carried here as integers.
`Candidate.gross_paise` is recomputed from its own legs, never taken from the
model and never re-derived from a fee model — `adjudication/` cannot import
`matching.fee_model`, and that restriction turns out to be a feature: the
arithmetic guardrail compares two integers that were both produced before the
LLM was ever called.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dcfield
from datetime import date
from typing import Any

#: Bumped whenever the serialized question changes shape. It is part of every
#: cache key, so an old cached answer can never be served against a new
#: question — the failure mode that would make "cached and deterministic" a lie.
CONTRACT_VERSION = "l4-3"

#: The answer "none of these combinations is supported by the evidence".
#:
#: Not decoration. On seed 42 the solver's five candidates are ALL wrong — the
#: true combination reconstructs 53 paise below the target and the rounding
#: tolerance is 50, so it is excluded before the model is ever asked. A forced
#: choice between five wrong options is a question with no right answer, and a
#: model that must answer it will answer it confidently. Letting the adjudicator
#: decline is what keeps the credit an honest exception instead of a wrong match
#: at 0.94 confidence.
NO_SELECTION = "NONE"


@dataclass(frozen=True, slots=True)
class CandidateLeg:
    """One ledger row inside a candidate combination.

    `gross_equivalent_paise` is the row as L3 valued it: an order's own amount,
    or a refund converted into gross space (a refund is deducted after the MDR,
    so its gross-equivalent is larger than its face value — see
    `matching.subset_matcher._gross_equivalents`). Signed, so refunds are
    negative and one sum compares to one target.

    `capture_date` and `settlement_id` are the two signals arithmetic cannot
    see, and the reason job A is worth asking a model at all.
    """

    order_id: str
    gross_equivalent_paise: int
    capture_date: date
    settlement_id: str | None = None

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "gross_paise": self.gross_equivalent_paise,
            "capture_date": self.capture_date.isoformat(),
            "settlement_id": self.settlement_id or "",
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """One combination of ledger rows that explains a credit exactly."""

    id: str
    legs: tuple[CandidateLeg, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("candidate id must be non-empty")
        if not self.legs:
            raise ValueError(f"candidate {self.id} has no legs")

    @property
    def order_ids(self) -> tuple[str, ...]:
        return tuple(leg.order_id for leg in self.legs)

    @property
    def gross_paise(self) -> int:
        """Summed from the legs, every time. Never stored, never supplied.

        A stored total is a number that can drift away from the rows it claims
        to describe; a recomputed one cannot. This is the value the arithmetic
        guardrail compares against the target.
        """
        return sum(leg.gross_equivalent_paise for leg in self.legs)

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "orders": list(self.order_ids),
            "capture_dates": [leg.capture_date.isoformat() for leg in self.legs],
            "settlement_ids": [leg.settlement_id or "" for leg in self.legs],
            "gross_paise": self.gross_paise,
        }


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """A credit that several combinations explain equally well. Job A input.

    Arithmetic has done all it can; separating these needs the narration, the
    settlement batch or the capture timing.
    """

    credit_utr: str
    credit_paise: int
    narration: str
    candidates: tuple[Candidate, ...]
    target_paise: int
    tolerance_paise: int = 0
    credit_value_date: date | None = None
    inferred_fee_rate: float | None = None
    #: Whether the solver enumerated EVERY combination that fits, or stopped at
    #: its cap. When it stopped, the right answer may not be on the list, and
    #: the model is told so — a candidate list presented as complete when it is
    #: a sample is how a plausible wrong answer gets manufactured.
    candidates_are_exhaustive: bool = True

    def __post_init__(self) -> None:
        if len(self.candidates) < 2:
            # One candidate is not an ambiguity — L3 would simply have proposed
            # it. Allowing a single-candidate "ambiguity" would let a matcher
            # launder an ordinary match through the LLM.
            raise ValueError(
                f"{self.credit_utr}: an ambiguity needs at least two candidates"
            )
        ids = [c.id for c in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.credit_utr}: duplicate candidate ids {ids}")

    @property
    def options(self) -> tuple[tuple[str, ...], ...]:
        """The combinations as bare order-id tuples."""
        return tuple(c.order_ids for c in self.candidates)

    def by_id(self, candidate_id: str) -> Candidate | None:
        for candidate in self.candidates:
            if candidate.id == candidate_id:
                return candidate
        return None

    def as_prompt_dict(self) -> dict[str, Any]:
        """The question, in the shape §4.4's job A payload describes."""
        return {
            "bank_credit": {
                "utr": self.credit_utr,
                "amount_paise": self.credit_paise,
                "value_date": (
                    self.credit_value_date.isoformat()
                    if self.credit_value_date
                    else ""
                ),
                "narration": self.narration,
            },
            "inferred_fee_rate": self.inferred_fee_rate,
            "expected_gross_paise": self.target_paise,
            "tolerance_paise": self.tolerance_paise,
            "candidates_are_exhaustive": self.candidates_are_exhaustive,
            "candidates": [c.as_prompt_dict() for c in self.candidates],
        }

    def cache_key(self) -> str:
        return _digest("A", self.as_prompt_dict())


@dataclass(frozen=True, slots=True)
class UnexplainedEvidence:
    """What L3 saw when nothing added up — job B's raw material.

    Separate from `Unexplained` because the two are known by different layers.
    L3 knows the credit, the target it back-solved and the rows that came
    closest; it does not know, and should not know, which buttons the exception
    UI offers. `pipeline/` is the layer allowed to see both, so it is the layer
    that attaches the menu.
    """

    ref: str
    amount_paise: int
    narration: str
    expected_gross_paise: int
    nearest_rows: tuple[CandidateLeg, ...] = ()
    #: Every open ledger row in the window, and what they add up to. Without
    #: this the model cannot tell "₹80 short of a near miss" from "the entire
    #: remaining pool is ₹2,685 against a ₹24,386 target" — two situations with
    #: completely different reason codes and completely different actions.
    open_pool_rows: int = 0
    open_pool_paise: int = 0
    value_date: date | None = None
    inferred_fee_rate: float | None = None

    def offering(
        self, classifications: tuple[str, ...], actions: tuple[str, ...]
    ) -> Unexplained:
        return Unexplained(
            ref=self.ref,
            amount_paise=self.amount_paise,
            narration=self.narration,
            expected_gross_paise=self.expected_gross_paise,
            nearest_rows=self.nearest_rows,
            open_pool_rows=self.open_pool_rows,
            open_pool_paise=self.open_pool_paise,
            allowed_classifications=classifications,
            allowed_actions=actions,
            value_date=self.value_date,
            inferred_fee_rate=self.inferred_fee_rate,
        )


@dataclass(frozen=True, slots=True)
class Unexplained:
    """A credit no combination explains. Job B input.

    `allowed_classifications` and `allowed_actions` are passed IN rather than
    imported, for two reasons. `adjudication/` may not import `exceptions_/`
    (§3.2) — and, more usefully, a model that must choose from an enumerated
    list can be checked against that list. An invented reason code, or an action
    with no button behind it, is caught by a set membership test rather than by
    hope.
    """

    ref: str
    amount_paise: int
    narration: str
    expected_gross_paise: int
    nearest_rows: tuple[CandidateLeg, ...]
    allowed_classifications: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    open_pool_rows: int = 0
    open_pool_paise: int = 0
    value_date: date | None = None
    inferred_fee_rate: float | None = None

    def __post_init__(self) -> None:
        if not self.allowed_classifications:
            raise ValueError(f"{self.ref}: no classifications offered to choose from")
        if not self.allowed_actions:
            raise ValueError(f"{self.ref}: no actions offered to choose from")

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "bank_credit": {
                "ref": self.ref,
                "amount_paise": self.amount_paise,
                "value_date": self.value_date.isoformat() if self.value_date else "",
                "narration": self.narration,
            },
            "inferred_fee_rate": self.inferred_fee_rate,
            "expected_gross_paise": self.expected_gross_paise,
            "open_pool": {
                "rows": self.open_pool_rows,
                "total_gross_paise": self.open_pool_paise,
            },
            "nearest_open_rows": [leg.as_prompt_dict() for leg in self.nearest_rows],
            "allowed_classifications": list(self.allowed_classifications),
            "allowed_actions": list(self.allowed_actions),
        }

    def cache_key(self) -> str:
        return _digest("B", self.as_prompt_dict())


@dataclass(frozen=True, slots=True)
class Verdict:
    """Job A's answer, once it has survived `adjudication.guardrails.verify`.

    `confidence` is the model's own number, and it is never the last word: the
    adjudicator caps it below the auto-post threshold, because a model choosing
    between two arithmetically identical answers should produce a prepared entry
    for a human to approve, not a posting.
    """

    selected: str
    confidence: float
    reason: str
    evidence_fields: tuple[str, ...] = ()
    prompt_version: str = ""
    model: str = ""
    #: "cache" or "api" — how this verdict was obtained, for the audit trail.
    origin: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def is_abstention(self) -> bool:
        """The adjudicator looked and declined to choose.

        A first-class answer, not a failure. It produces no match and leaves an
        honest exception — which on a question where every option is wrong is
        the only correct outcome available.
        """
        return self.selected == NO_SELECTION


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """Job B's answer: the WHY and the ACTION on an exception card (§8.2).

    This is where the LLM earns its place. Code can rank near-misses; only the
    model can say why ₹80 is missing in language a controller can act on.
    """

    classification: str
    hypothesis: str
    suggested_action: str
    confidence: float
    prompt_version: str = ""
    model: str = ""
    origin: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")


@dataclass(frozen=True, slots=True)
class AdjudicationResult:
    """Everything one L4 pass produced. What the orchestrator consumes.

    Both maps are keyed, never positional. A model that returns three answers to
    four questions would otherwise shift every verdict onto the wrong credit —
    silently, and in the direction of a wrong journal entry.
    """

    #: credit UTR -> the verdict that survived the guardrails
    verdicts: dict[str, Verdict] = dcfield(default_factory=dict)
    #: credit UTR -> the guardrail code that threw the verdict out
    rejections: dict[str, str] = dcfield(default_factory=dict)
    #: credit UTR -> the reason the adjudicator declined to choose at all
    abstentions: dict[str, str] = dcfield(default_factory=dict)
    #: exception ref -> the accepted job B hypothesis
    hypotheses: dict[str, Hypothesis] = dcfield(default_factory=dict)
    #: exception ref -> the guardrail code that threw the hypothesis out
    hypothesis_rejections: dict[str, str] = dcfield(default_factory=dict)

    #: Cases that REACHED L4. This is the numerator in §2.2's <10% budget:
    #: a cached answer still means the record needed adjudicating.
    calls: int = 0
    #: HTTP requests actually made. Zero on a fully cached run, which is why it
    #: is reported apart from `calls` rather than folded into it.
    api_requests: int = 0
    cost_paise: int = 0
    #: Refs the budget would not stretch to. They stay exceptions and are named,
    #: because "we ran out of budget" is a fact a controller is entitled to.
    skipped_over_budget: tuple[str, ...] = ()
    #: Anything the run wants on the record: a corrupt cache entry, a missing
    #: credential, a refusal. Surfaced, never swallowed (§5.5).
    notes: tuple[str, ...] = ()


def _digest(job: str, payload: dict[str, Any]) -> str:
    """A stable fingerprint of one question.

    `sort_keys` matters more than it looks: an unsorted dump hashes the same
    question two ways, which turns the cache into a coin flip and the "two
    identical runs" requirement into a coin flip with it.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{CONTRACT_VERSION}-{job}-{hashlib.sha256(blob.encode()).hexdigest()[:32]}"
