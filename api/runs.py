"""The run store — one reconciliation, held so a screen can act on it.
Guide §5.7, §8.

A `RunResult` is immutable and pure: it is what the pipeline produced, and the
eval scores exactly that. But the UI does things *to* a run — approves an entry,
posts a credit to suspense, reverses it — and those are not pipeline outputs.
They are decisions a human made afterwards.

So a `Run` holds three things side by side:

* the immutable `RunResult`,
* the **book** the run posted into, which keeps issuing journal numbers as a
  controller acts, and
* the **audit trail**, which is where "resolved by hand 9, of those reversed 1"
  comes from.

Nothing here recomputes a figure the pipeline already produced. The header total
is the pipeline's; what this module adds is only what happened after.

**Runs are keyed by (seed, scale, ablation)**, so asking for seed 42 twice
returns the same run rather than re-reconciling — and asking for seed 7 builds a
second one alongside it. That is what makes the gate-12 check ("start on 42,
then on 7, every figure must change") a matter of one query parameter instead of
a restart.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from adjudication.protocols import Adjudicator
from core.config import Settings
from core.run_result import RunResult
from persistence.repositories import InMemoryJournalRepository
from pipeline.audit import AuditTrail

DEFAULT_SEED = 42
DEFAULT_SCALE = 500


@dataclass
class Run:
    """One reconciliation, plus everything a controller has done to it."""

    run_id: str
    seed: int
    scale: int
    dataset: Path
    result: RunResult
    repository: InMemoryJournalRepository
    trail: AuditTrail
    settings: Settings
    started_at: datetime
    elapsed_ms: float
    no_llm: bool = False
    closed_at: datetime | None = None
    #: Review items a human has decided on: utr -> "approved" | "rejected".
    review_decisions: dict[str, str] = field(default_factory=dict)
    #: Exception ref -> the outcome of the action last executed on it, so the
    #: card can render its post-action state (frame 3a) and offer the reversal.
    action_outcomes: dict[str, object] = field(default_factory=dict)

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def label(self) -> str:
        """`seed 42 · 500 orders`. Shown wherever the run identifies itself."""
        return f"seed {self.seed} · {self.scale} orders"


class RunStore:
    """Builds runs on demand and remembers them.

    Thread-safe because uvicorn will happily serve two requests at once, and two
    concurrent first-hits on the same seed would otherwise reconcile the dataset
    twice and hand out two different books — with two different journal numbers
    for the same entry.
    """

    def __init__(self, data_root: Path | None = None) -> None:
        self._root = data_root or Path("data")
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    @staticmethod
    def key(seed: int, scale: int, *, no_llm: bool) -> str:
        suffix = "-no-llm" if no_llm else ""
        return f"seed{seed}-{scale}{suffix}"

    def dataset_for(self, seed: int, scale: int) -> Path:
        return self._root / f"seed{seed}"

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def all(self) -> tuple[Run, ...]:
        return tuple(self._runs.values())

    def ensure(
        self, seed: int = DEFAULT_SEED, scale: int = DEFAULT_SCALE, *, no_llm: bool = False
    ) -> Run:
        """The run for this seed, reconciling it the first time it is asked for."""
        run_id = self.key(seed, scale, no_llm=no_llm)
        existing = self._runs.get(run_id)
        if existing is not None:
            return existing

        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                return existing
            run = self._build(run_id, seed, scale, no_llm=no_llm)
            self._runs[run_id] = run
            return run

    def rebuild(self, run: Run) -> Run:
        """Re-reconcile from source, discarding the book and the trail.

        This is the "Re-run reconciliation" button (frame 3b) and the RERUN
        action. It deliberately throws away what a controller did: re-running is
        how you check that the deterministic core still reaches the same answer,
        and carrying manual corrections across would make that check meaningless.
        """
        with self._lock:
            fresh = self._build(run.run_id, run.seed, run.scale, no_llm=run.no_llm)
            self._runs[run.run_id] = fresh
            return fresh

    # ------------------------------------------------------------------

    def _build(self, run_id: str, seed: int, scale: int, *, no_llm: bool) -> Run:
        # Imported here rather than at module scope so that importing the API
        # does not drag in the whole pipeline — `api/` is a delivery mechanism
        # (§3.2), and its import graph should say so.
        from api.deps import SystemClock
        from core.dates import BusinessCalendar
        from matching.registry import build_strategies
        from pipeline.orchestrator import ReconciliationPipeline

        dataset = self.dataset_for(seed, scale)
        if not dataset.exists():
            # The runner, not make: this string is the one error a visitor to
            # the hosted demo is most likely to see, and `make` is not on PATH
            # on a default Windows install — which is exactly where telling
            # somebody to run it wastes their next ten minutes.
            raise DatasetMissing(
                f"no dataset at {dataset} — run "
                f"`python tasks.py generate --seed {seed} --scale {scale}` first"
            )

        settings = Settings()
        repository = InMemoryJournalRepository()
        clock = SystemClock()

        adjudicator = _adjudicator(no_llm=no_llm)
        pipeline = ReconciliationPipeline(
            build_strategies(),
            calendar=BusinessCalendar(),
            settings=settings,
            adjudicator=adjudicator,
            repository=repository,
        )

        t0 = time.perf_counter()
        result = pipeline.run(dataset)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        return Run(
            run_id=run_id,
            seed=seed,
            scale=scale,
            dataset=dataset,
            result=result,
            repository=repository,
            trail=AuditTrail(clock),
            settings=settings,
            started_at=clock.now(),
            elapsed_ms=elapsed_ms,
            no_llm=no_llm,
        )


def _adjudicator(*, no_llm: bool) -> Adjudicator:
    from adjudication.llm_adjudicator import LlmAdjudicator
    from adjudication.null_adjudicator import NullAdjudicator

    return NullAdjudicator() if no_llm else LlmAdjudicator()


class DatasetMissing(FileNotFoundError):
    """Asked for a seed nobody has generated. A 404 with instructions, not a 500."""
