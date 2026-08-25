"""Agent construction — the seam the eval harness runs against. Guide §5.4, §7.3.

`build_pipeline()` is the single entry point the eval calls. It returns something
satisfying the `Agent` protocol, and the eval scores *only* what that returns
(§7.3, and the gate 3 stop condition).

Until L0-L5 exist, it returns `StubAgent`: an agent that resolves nothing. The
eval therefore scores it at 0%, which is the correct result at gate 3 — a
harness that cannot report a failing score cannot be trusted to report a passing
one either.

Each gate from 4 onward replaces one more of the stub's layers. Nothing in the
eval changes when that happens, because the eval only ever sees `RunResult`.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

from core.run_result import RunResult


class Agent(Protocol):
    """Anything that can turn a dataset directory into a RunResult."""

    name: str

    def run(self, dataset: Path) -> RunResult: ...


class StubAgent:
    """Resolves nothing. The gate 3 placeholder.

    It does read the bank file, so `records_processed` and the throughput
    measurement are real rather than fabricated — but it makes no claim about a
    single credit. Declining to answer is the honest behaviour for a system that
    has no matcher yet, and it is exactly what a 0% score should look like.
    """

    name = "stub"

    def run(self, dataset: Path) -> RunResult:
        bank = dataset / "bank.csv"
        rows = 0
        if bank.exists():
            with bank.open(encoding="utf-8") as fh:
                rows = sum(1 for _ in csv.DictReader(fh))
        return RunResult(matches={}, exceptions=(), records_processed=rows)


def build_pipeline(*, no_llm: bool = False, no_fee_model: bool = False) -> Agent:
    """Wire an agent.

    `no_llm` selects the NullAdjudicator path (§4.4) and `no_fee_model` disables
    L2 so its contribution can be quantified by ablation (§7.5). Layers appear
    here as their gates land; the eval never changes, because it only ever sees
    `RunResult`.
    """
    from adjudication.llm_adjudicator import LlmAdjudicator
    from adjudication.null_adjudicator import NullAdjudicator
    from adjudication.protocols import Adjudicator
    from core.config import Settings
    from core.dates import BusinessCalendar
    from matching.registry import build_strategies
    from pipeline.orchestrator import ReconciliationPipeline

    settings = Settings()
    # The ONE place the LLM is chosen. Everything downstream sees the protocol,
    # which is why every test in this repo runs without an API key (§5.2).
    adjudicator: Adjudicator = NullAdjudicator() if no_llm else LlmAdjudicator()
    # ONE calendar instance, shared by every layer that reasons about dates.
    # Two would make planted HOLIDAY_SHIFT cases unsolvable by construction,
    # and that bug looks exactly like a matcher failure (§5.1).
    calendar = BusinessCalendar()

    return ReconciliationPipeline(
        build_strategies(no_fee_model=no_fee_model),
        calendar=calendar,
        settings=settings,
        fee_model_enabled=not no_fee_model,
        adjudicator=adjudicator,
    )
