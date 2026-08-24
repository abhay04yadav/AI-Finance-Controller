"""The subset solver, as pure arithmetic. Guide §4.3c, §5.4 (SRP).

This file never imports a Record, a Money or a date. That is the point: the
solver is integers in, indices out, so it can be property-tested exhaustively
without constructing a payments world — and so the payments rules stay in
`subset_matcher.py` where they can be read.
"""

from __future__ import annotations

import ast
import inspect
import random
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from matching.subset_solver import DFS_MAX, MITM_MAX, SolveResult, solve

VALUES = st.lists(st.integers(-50_000, 500_000), min_size=1, max_size=15)


# ==========================================================================
# The §4.3 property test: any subset we plant must be found
# ==========================================================================


@given(VALUES, st.randoms())
@settings(max_examples=1000, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_l3_solver_is_correct(values: list[int], rng: random.Random) -> None:
    """1,000 random cases. If a subset sums to the target, it must be found."""
    k = rng.randint(1, len(values))
    picked = rng.sample(range(len(values)), k)
    target = sum(values[i] for i in picked)

    result = solve(values, target, tol=0, max_solutions=25, max_nodes=400000)
    sums = [sum(values[i] for i in s) for s in result.solutions]

    assert result.solutions, f"found nothing for {values} -> {target}"
    assert all(s == target for s in sums), "returned a subset that does not sum"
    if result.exhausted:
        assert tuple(sorted(picked)) in result.solutions


@given(VALUES, st.integers(-10_000, 10_000))
@settings(max_examples=400, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_every_returned_subset_actually_sums_to_the_target(
    values: list[int], target: int
) -> None:
    """Soundness, separately from completeness: never return a wrong answer."""
    result = solve(values, target, tol=0, max_solutions=5, max_nodes=160000)
    for indices in result.solutions:
        assert sum(values[i] for i in indices) == target
        assert len(set(indices)) == len(indices), "an index was used twice"


@given(VALUES)
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_indices_are_always_in_range(values: list[int]) -> None:
    result = solve(values, sum(values), tol=0, max_nodes=160000)
    for indices in result.solutions:
        assert all(0 <= i < len(values) for i in indices)


# ==========================================================================
# Negative values — the trap §4.3c names
# ==========================================================================


def test_finds_a_combination_containing_a_refund() -> None:
    """§4.3a's worked example: 2000 + 3500 + 2500 − 1200 = 6800.

    Textbook subset-sum DP assumes non-negative values and would miss this
    entirely — silently, for every refund case in the dataset.
    """
    values = [200_000, 350_000, 250_000, -120_000]
    result = solve(values, 680_000, tol=0)
    assert (0, 1, 2, 3) in result.solutions


def test_solves_when_the_answer_is_mostly_negative() -> None:
    values = [-100, -200, -300, 50]
    assert (0, 1) in solve(values, -300, tol=0).solutions


@given(st.lists(st.integers(-100_000, -1), min_size=2, max_size=10))
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_all_negative_pools_still_work(values: list[int]) -> None:
    result = solve(values, sum(values), tol=0, max_nodes=160000)
    assert tuple(range(len(values))) in result.solutions


def test_the_solver_contains_no_dynamic_programming_table() -> None:
    """A reviewer asks which algorithm. DP over a value table cannot represent
    negative values without an offset correction per subset size (§4.3c)."""
    from matching import subset_solver

    src = inspect.getsource(subset_solver)
    assert "dfs" in src.lower()
    assert "meet_in_the_middle" in src


# ==========================================================================
# Tolerance
# ==========================================================================


def test_tolerance_admits_a_near_miss() -> None:
    """ROUNDING_DRIFT: a genuine pair off by a few paise (§4.2 rounding note)."""
    values = [100_000, 200_000]
    assert not solve(values, 300_030, tol=0).solutions
    assert (0, 1) in solve(values, 300_030, tol=50).solutions


def test_tolerance_does_not_admit_a_real_mismatch() -> None:
    assert not solve([100_000, 200_000], 350_000, tol=50).solutions


# ==========================================================================
# Ambiguity is information, not a nuisance (§4.3d)
# ==========================================================================


def test_collects_several_solutions_rather_than_stopping_at_the_first() -> None:
    """Two identical amounts mean two valid answers. Returning one and moving on
    would manufacture confidence the data does not support."""
    values = [100, 100, 50, 50]
    result = solve(values, 150, tol=0, max_solutions=5)
    assert len(result.solutions) >= 2
    assert result.is_ambiguous


def test_respects_max_solutions() -> None:
    values = [10] * 12
    result = solve(values, 30, tol=0, max_solutions=3)
    assert len(result.solutions) == 3
    assert not result.exhausted, "must admit the search was cut short"


def test_solutions_are_deduplicated_and_ordered() -> None:
    result = solve([5, 5, 5], 10, tol=0, max_solutions=10)
    assert len(result.solutions) == len(set(result.solutions))
    assert list(result.solutions) == sorted(result.solutions)


# ==========================================================================
# Budget: never hang, and say so when cut short (§4.3c)
# ==========================================================================


def test_a_wide_pool_returns_rather_than_hanging() -> None:
    values = list(range(1, 200))  # far past DFS_MAX
    start = time.perf_counter()
    result = solve(values, 999_999, tol=0, max_nodes=40000)
    assert (time.perf_counter() - start) * 1000 < 200
    assert not result.solutions
    assert not result.exhausted, "an unsearched pool must not look like an empty one"


def test_exhausted_distinguishes_not_there_from_not_found_yet() -> None:
    """A matcher that conflated these would report a confident exception for a
    credit it had simply not finished looking at."""
    genuine = solve([1, 2, 3], 100, tol=0)
    assert not genuine.solutions
    assert genuine.exhausted

    unsearched = solve(list(range(1, 200)), 12_345, tol=0)
    assert not unsearched.exhausted


def test_stays_inside_its_work_budget() -> None:
    values = [random.Random(1).randint(1, 500_000) for _ in range(DFS_MAX)]
    start = time.perf_counter()
    solve(values, sum(values) // 2 + 7, tol=0, max_nodes=40000)
    assert (time.perf_counter() - start) * 1000 < 150


@pytest.mark.parametrize("n", [DFS_MAX, DFS_MAX + 1, MITM_MAX, MITM_MAX + 1])
def test_every_pool_size_returns_promptly(n: int) -> None:
    rng = random.Random(n)
    values = [rng.randint(-50_000, 500_000) for _ in range(n)]
    start = time.perf_counter()
    result = solve(values, sum(values[: n // 2]), tol=0, max_nodes=40000)
    elapsed = (time.perf_counter() - start) * 1000
    assert elapsed < 100, f"n={n} took {elapsed:.0f} ms"
    assert isinstance(result, SolveResult)


def test_meet_in_the_middle_finds_what_dfs_would() -> None:
    """The two engines must agree, or pool size silently changes the answer."""
    rng = random.Random(7)
    values = [rng.randint(1, 100_000) for _ in range(MITM_MAX)]
    target = sum(values[3:9])
    result = solve(values, target, tol=0, max_solutions=25, max_nodes=1600000)
    assert tuple(range(3, 9)) in result.solutions


# ==========================================================================
# Edge cases
# ==========================================================================


def test_empty_pool() -> None:
    result = solve([], 100, tol=0)
    assert not result.solutions
    assert result.exhausted


def test_the_empty_subset_is_never_an_answer() -> None:
    """Zero explains nothing. Returning it would let a credit be 'matched' by
    no ledger rows at all."""
    assert not solve([5, 10], 0, tol=0).solutions


def test_a_single_value_pool() -> None:
    assert (0,) in solve([500], 500, tol=0).solutions


def test_zero_values_do_not_create_phantom_solutions() -> None:
    result = solve([0, 100], 100, tol=0, max_solutions=5)
    assert all(1 in s for s in result.solutions)


def test_is_deterministic() -> None:
    rng = random.Random(3)
    values = [rng.randint(-10_000, 100_000) for _ in range(15)]
    a = solve(values, 150_000, tol=100, max_solutions=5)
    b = solve(values, 150_000, tol=100, max_solutions=5)
    assert a.solutions == b.solutions


# ==========================================================================
# Purity — the gate 7 review question
# ==========================================================================


def test_the_solver_knows_nothing_about_payments() -> None:
    """"Does the solver import anything about payments?" — it must not.

    If it knew about fees or dates it could not be property-tested with plain
    integers, and the design would already have drifted (§5.4).
    """
    from matching import subset_solver

    tree = ast.parse(inspect.getsource(subset_solver))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"core", "ingest", "matching", "generator", "eval"}, (
        f"the solver imports domain modules: {imported}"
    )
    # Code only, via the same tokenizer the standing drift check uses. The
    # module docstring says "no fees, no dates, no records" — describing the
    # rule is not breaking it, and this is the third place a naive text scan
    # would have got that wrong.
    import importlib.util
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "drift_check", root / "scripts" / "drift_check.py"
    )
    assert spec and spec.loader
    drift = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drift)

    solver_path = root / "matching" / "subset_solver.py"
    for word in ("fee", "refund", "ledger", "utr", "money", "paise"):
        hits = drift.scan([solver_path], rf"{word}", )
        assert not hits, f"the solver's code mentions {word!r}: {hits}"


def test_solver_and_matcher_are_separate_files() -> None:
    from matching import subset_matcher, subset_solver

    assert subset_solver.__file__ != subset_matcher.__file__
