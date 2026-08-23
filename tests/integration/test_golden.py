"""Golden regression. Guide §7.5, §9.7.

A frozen dataset and its expected metrics. CI fails if precision drops, so a
change that quietly makes the agent worse cannot merge.

The expectations in `tests/golden/expected_metrics.json` are **deliberately
updated**, never auto-refreshed: when a gate genuinely improves the score, the
new numbers are committed as a decision. A golden file that moves whenever the
code moves is not a regression test.

At gate 3 the expected precision is 0.0 because the agent is a stub. That is the
correct starting expectation, and it is what makes the first real match visible
as a change rather than as noise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.evaluate import ensure_dataset, evaluate

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
EXPECTED = GOLDEN_DIR / "expected_metrics.json"

GOLDEN_SEED = 42
GOLDEN_SCALE = 200


@pytest.fixture(scope="module")
def golden_metrics(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("golden")
    ensure_dataset(out, GOLDEN_SEED, GOLDEN_SCALE)
    return evaluate(out)


def expected() -> dict:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_golden_expectations_exist() -> None:
    assert EXPECTED.exists(), (
        "tests/golden/expected_metrics.json is the regression baseline — "
        "regenerate it deliberately, never automatically"
    )


def test_dataset_shape_has_not_drifted(golden_metrics) -> None:
    """If the generator changes shape, every historical metric becomes
    incomparable. Catching that here beats discovering it mid-demo."""
    exp = expected()
    assert golden_metrics.total == exp["total"]
    assert golden_metrics.planted == exp["planted"]


def test_precision_has_not_regressed(golden_metrics) -> None:
    """The CI gate: a change that lowers precision does not merge."""
    floor = expected()["min_match_precision"]
    assert golden_metrics.match_precision >= floor, (
        f"match precision {golden_metrics.match_precision:.4f} fell below the "
        f"golden floor {floor:.4f}"
    )


def test_match_rate_has_not_regressed(golden_metrics) -> None:
    floor = expected()["min_match_rate"]
    assert golden_metrics.match_rate >= floor


def test_exception_recall_has_not_regressed(golden_metrics) -> None:
    floor = expected()["min_exception_recall"]
    assert golden_metrics.exception_recall >= floor


def test_llm_budget_is_respected(golden_metrics) -> None:
    """§2.2: under 10% of records may reach L4. Asserted from gate 3 onward so
    the budget can never be quietly exceeded once L4 lands at gate 11."""
    ceiling = expected()["max_llm_call_ratio"]
    total = max(golden_metrics.total, 1)
    assert golden_metrics.llm_calls / total <= ceiling


def test_scoring_is_reproducible(tmp_path: Path) -> None:
    """Two runs of the same seed produce the same fingerprint (§9.1)."""
    a = tmp_path / "a"
    ensure_dataset(a, GOLDEN_SEED, GOLDEN_SCALE)
    first = evaluate(a)
    second = evaluate(a)
    assert first.fingerprint == second.fingerprint
