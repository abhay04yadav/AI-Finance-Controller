"""Adjudicator protocol. Guide §5.2, §5.4 (DIP).

The orchestrator depends on this protocol, never on `LlmAdjudicator`. That is
what makes the entire pipeline testable without an API key, and what makes
swapping the provider a one-line change in `pipeline/factory.py`.

**The interface is batch-shaped on purpose.** §4.4 requires ambiguities to be
batched into one request and calls one-call-per-row the source of the
₹40-vs-₹0.31 cost gap. A `resolve(one_ambiguity)` signature — the shape the
guide sketches in §5.4 — makes the expensive mistake the natural one: any
implementer writes a loop, and the cost regression never shows up in a test.
Taking a whole sequence makes the correct behaviour the only expressible one.

`budget` is a hard cap on how many cases may reach L4 at all, in records, passed
down from `Settings.llm_budget_ratio`. An adjudicator that exceeds it is broken,
not expensive.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from core.adjudication import AdjudicationResult, Ambiguity, Unexplained


class Adjudicator(Protocol):
    """L4. Handles only what arithmetic could not settle."""

    name: str

    def adjudicate(
        self,
        ambiguities: Sequence[Ambiguity],
        cases: Sequence[Unexplained],
        *,
        budget: int,
    ) -> AdjudicationResult: ...
