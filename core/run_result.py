"""The public result contract — the ONLY surface the eval harness may see.
Guide §7, §5.4.

Gate 3's stop condition: *"the eval must not import anything from the agent's
internals to score itself. It must only read truth.json and the agent's public
output. Otherwise you're grading the exam with the answer key visible to the
student."*

So this module is the boundary. `pipeline/` produces a `RunResult`; `eval/`
consumes one, plus `truth.json`, and nothing else. Neither can reach past it:
`core/` imports nothing from the project, so this type cannot smuggle a
reference to a matcher, a context, or a candidate pool.

Everything here is what a *user* of the system would also be shown — the match,
its confidence, the reason it was made, and the evidence it rested on (§2.7
rule 4). If a field would not be defensible on an exception card or in an audit
trail, it does not belong in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import CashPosition, JournalEntry
from core.reason_codes import ReasonCode


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """A prepared entry awaiting a human decision. Guide §4.5, §8.4.

    Carries the entry that WOULD be posted, not just the suggested match:
    showing the prepared double-entry is what turns a two-minute investigation
    into a two-second Approve/Reject.
    """

    utr: str
    ledger_ids: frozenset[str]
    confidence: float
    reason: str
    prepared_entry: JournalEntry


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """One bank credit the agent claims to have explained.

    `ledger_ids` is scored with exact set equality (§7.3): two of three orders
    correct is wrong, not 67% right, because a half-matched settlement posts a
    wrong journal entry.
    """

    utr: str
    ledger_ids: frozenset[str]
    confidence: float
    strategy: str
    reason: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
        if not self.reason.strip():
            # §2.7 rule 4: no automated decision without a justification.
            raise ValueError(f"match on {self.utr} carries no reason")
        if not self.ledger_ids:
            # "This credit is explained by nothing" is not a match, it is an
            # exception. Allowing it would also be scoreable: the answer key
            # returns [] for an unmapped UTR, so an empty claim would compare
            # equal and harvest free precision.
            raise ValueError(f"match on {self.utr} claims no ledger rows")


@dataclass(frozen=True, slots=True)
class ExceptionOutcome:
    """One thing the agent could not resolve, and says so.

    The honest exception list the brief asks for. `what` and `why` are the
    controller-facing fields from §8.2.
    """

    ref: str
    reason_code: ReasonCode
    what: str = ""
    why: str = ""
    amount_paise: int | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one reconciliation run produced, as the outside world sees it.

    `matches` is keyed by bank UTR because that is the unit a credit arrives in,
    and the unit the answer key is keyed by.
    """

    matches: dict[str, MatchOutcome] = field(default_factory=dict)
    exceptions: tuple[ExceptionOutcome, ...] = ()
    records_processed: int = 0
    llm_calls: int = 0
    llm_cost_paise: int = 0
    #: Per-layer wall time, for the --profile answer to "which layer is slow?" (§9.6)
    layer_timings_ms: dict[str, float] = field(default_factory=dict)
    #: The MDR the system LEARNED. None when nothing could be inferred.
    #: Reported because "we were never told this" is the point (§2.3).
    fee_rate: float | None = None
    fee_model_summary: str = ""
    #: The books, once L5 has posted (§4.5). Empty before gate 8.
    entries: tuple[JournalEntry, ...] = ()
    #: Matches confident enough to show but not to post — the review queue.
    review_queue: tuple[ReviewItem, ...] = ()
    cash_position: CashPosition | None = None
    duplicate_postings_refused: int = 0

    def auto_posted(self, threshold: float) -> int:
        """Matches confident enough to post without asking a human (§4.5)."""
        return sum(1 for m in self.matches.values() if m.confidence >= threshold)
