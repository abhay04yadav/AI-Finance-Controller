"""Result[T, E] — expected failures are values, not exceptions. Guide §5.5."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from core.reason_codes import ReasonCode
from core.result import Err, Ok, Result, UnwrapError


def test_ok_carries_a_value() -> None:
    r = Ok(42)
    assert r.is_ok() and not r.is_err()
    assert r.unwrap() == 42


def test_err_carries_a_reason() -> None:
    r = Err("arithmetic_mismatch")
    assert r.is_err() and not r.is_ok()
    assert r.unwrap_err() == "arithmetic_mismatch"


def test_no_candidates_is_a_value_not_an_exception() -> None:
    """§5.5: "no candidates found" is Ok([]), which flows to the exception list
    rather than unwinding the stack."""
    r: Result[list[str], str] = Ok([])
    assert r.is_ok()
    assert r.unwrap() == []


def test_unwrapping_the_wrong_variant_is_a_bug_and_raises() -> None:
    with pytest.raises(UnwrapError):
        Err("boom").unwrap()
    with pytest.raises(UnwrapError):
        Ok(1).unwrap_err()


def test_unwrap_or_supplies_a_default_only_on_err() -> None:
    assert Ok(1).unwrap_or(99) == 1
    assert Err("boom").unwrap_or(99) == 99


# --------------------------------------------------------------------------
# Combinators
# --------------------------------------------------------------------------


def test_map_transforms_ok_and_passes_err_through() -> None:
    assert Ok(2).map(lambda x: x * 10) == Ok(20)
    assert Err("boom").map(lambda x: x * 10) == Err("boom")


def test_map_err_transforms_err_and_passes_ok_through() -> None:
    assert Err("boom").map_err(str.upper) == Err("BOOM")
    assert Ok(2).map_err(str.upper) == Ok(2)


def test_and_then_chains_and_short_circuits() -> None:
    assert Ok(2).and_then(lambda x: Ok(x + 1)) == Ok(3)
    assert Ok(2).and_then(lambda x: Err("rejected")) == Err("rejected")
    assert Err("first").and_then(lambda x: Ok(x + 1)) == Err("first")


# --------------------------------------------------------------------------
# Structural pattern matching — how the orchestrator consumes a verdict (§5.4)
# --------------------------------------------------------------------------


def test_match_statement_destructures_both_variants() -> None:
    def route(result: Result[str, str]) -> str:
        match result:
            case Ok(verdict):
                return f"accepted:{verdict}"
            case Err(code):
                return f"{ReasonCode.ADJUDICATION_REJECTED}:{code}"
            case _:  # pragma: no cover - exhaustive above
                raise AssertionError

    assert route(Ok("candidate-A")) == "accepted:candidate-A"
    assert route(Err("hallucinated_candidate")) == (
        "ADJUDICATION_REJECTED:hallucinated_candidate"
    )


# --------------------------------------------------------------------------
# Value semantics
# --------------------------------------------------------------------------


def test_variants_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        Ok(1).value = 2  # type: ignore[misc]


def test_equality_is_by_value_and_variant() -> None:
    assert Ok(1) == Ok(1)
    assert Err(1) == Err(1)
    assert Ok(1) != Err(1)


def test_variants_are_hashable() -> None:
    assert len({Ok(1), Ok(1), Err(1)}) == 2
