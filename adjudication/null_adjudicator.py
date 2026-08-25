"""NullAdjudicator — the --no-llm path. Null Object pattern. Guide §4.4, §5.3.

Returns "no verdict" for every ambiguity, sending them all to exception. Zero
conditionals in the orchestrator: there is no `if self._adjudicator is not None`
anywhere, because the absence of an adjudicator is itself an adjudicator.

This is a FIRST-CLASS SUPPORTED MODE, not a debug flag. `--no-llm` must produce
a working system at ~94% match rate and >= 98% precision (gate 10). It is both
an engineering safeguard and the strongest single line in the demo: *AI is our
last mile, not our crutch.*

Note what it does NOT do: it does not flag anything. Returning an empty result
leaves L3's own AMBIGUOUS_UNADJUDICATED flag standing, which is the truthful
code for this run — nothing was adjudicated, so nothing was rejected. Reusing
ADJUDICATION_REJECTED here would make the exception page claim the AI tried and
failed on a run where no AI existed.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.adjudication import AdjudicationResult, Ambiguity, Unexplained


class NullAdjudicator:
    """Declines every case, costs nothing, and says so."""

    name = "L4_null"

    def adjudicate(
        self,
        ambiguities: Sequence[Ambiguity],
        cases: Sequence[Unexplained],
        *,
        budget: int,
    ) -> AdjudicationResult:
        note = (
            f"--no-llm: {len(ambiguities)} ambiguity(ies) and {len(cases)} "
            "unexplained credit(s) left to the exception list unadjudicated."
        )
        return AdjudicationResult(notes=(note,) if (ambiguities or cases) else ())
