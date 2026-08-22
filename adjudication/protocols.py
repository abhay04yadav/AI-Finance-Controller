"""Adjudicator protocol. Guide §5.2, §5.4 (DIP).

The orchestrator depends on this protocol, never on LlmAdjudicator. That is what
makes the entire pipeline testable without an API key, and what makes swapping
the LLM provider a one-line change in api/deps.py (§5.9 scenario 2).
"""

from typing import Protocol


class Adjudicator(Protocol):
    def resolve(self, ambiguity: "object") -> "object":  # -> Result[Verdict, str]
        ...
