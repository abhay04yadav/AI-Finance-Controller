"""Gate 0 verification: every package in guide §3.2 exists and imports cleanly.

This is the only test that exists at gate 0. It asserts structure, not behaviour.
Gate 1 replaces the placeholder assertions with real ones.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The package list from guide §3.2. Flattening this is the gate 0 stop condition.
PACKAGES = [
    "core",
    "ingest",
    "matching",
    "adjudication",
    "posting",
    "exceptions_",
    "pipeline",
    "api",
    "persistence",
    "generator",
    "eval",
]

MODULES = [
    "core.money",
    "core.dates",
    "core.models",
    "core.reason_codes",
    "core.result",
    "core.config",
    "ingest.protocols",
    "ingest.ledger_adapter",
    "ingest.settlement_adapter",
    "ingest.bank_adapter",
    "ingest.normalizer",
    "matching.protocols",
    "matching.exact_matcher",
    "matching.fee_model",
    "matching.subset_matcher",
    "matching.subset_solver",
    "matching.registry",
    "adjudication.protocols",
    "adjudication.llm_adjudicator",
    "adjudication.null_adjudicator",
    "adjudication.schemas",
    "adjudication.guardrails",
    "posting.protocols",
    "posting.chart_of_accounts",
    "posting.journal_builder",
    "posting.confidence_router",
    "posting.cash_position",
    "exceptions_.classifier",
    "exceptions_.actions",
    "pipeline.orchestrator",
    "pipeline.audit",
    "pipeline.debug",
    "api.main",
    "api.deps",
    "persistence.repositories",
    "generator.generate",
    "generator.writers",
    "generator.injectors",
    "eval.evaluate",
    "eval.metrics",
]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_directory_exists(pkg: str) -> None:
    assert (ROOT / pkg).is_dir(), f"guide §3.2 requires the {pkg}/ package"


@pytest.mark.parametrize("mod", MODULES)
def test_module_imports(mod: str) -> None:
    importlib.import_module(mod)


def test_reason_codes_complete() -> None:
    """All 12 codes from Appendix A exist."""
    from core.reason_codes import ReasonCode

    assert len(ReasonCode) == 12


def test_chart_of_accounts_complete() -> None:
    """All 7 accounts from Appendix B exist."""
    from posting.chart_of_accounts import Account

    assert len(Account) == 7


def test_drift_check_passes() -> None:
    """The six standing checks from the Review Guide, part 3.

    Delegates to scripts/drift_check.py rather than reimplementing the scans, so
    there is exactly one definition of what counts as drift. Covers: no floats in
    the money path, no wall clock in business logic, no swallowed errors,
    layering intact, and no LLM client outside adjudication/ before gate 11.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "drift_check.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_layering_rule_holds() -> None:
    """core/ imports nothing from this project; the rest respect §3.2."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_layering.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
