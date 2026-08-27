"""The eval harness. Guide §7.

The harness decides what every later gate is allowed to claim, so its own
correctness has to be established before there is anything to score.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.config import Settings
from core.reason_codes import ReasonCode
from core.run_result import ExceptionOutcome, MatchOutcome, RunResult
from eval.evaluate import (
    TruthVersionError,
    ensure_dataset,
    evaluate,
    is_correct,
    load_truth,
    score,
)

SETTINGS = Settings()


def truth(mappings: dict[str, list[str]], exceptions: list[tuple[str, str]] | None = None):
    return {
        "generator_version": "1.0.0",
        "seed": 42,
        "scale": 100,
        "mappings": mappings,
        "exceptions": [{"ref": r, "type": t} for r, t in (exceptions or [])],
    }


def match(utr: str, ids: list[str], confidence: float = 1.0) -> MatchOutcome:
    return MatchOutcome(
        utr=utr,
        ledger_ids=frozenset(ids),
        confidence=confidence,
        strategy="test",
        reason="test fixture",
    )


# ==========================================================================
# THE scoring rule: exact set equality (§7.3)
# ==========================================================================


def test_exact_set_equality_is_the_rule() -> None:
    assert is_correct(frozenset({"A", "B", "C"}), ["A", "B", "C"])
    assert is_correct(frozenset({"C", "A", "B"}), ["A", "B", "C"])  # order is irrelevant


def test_two_of_three_is_wrong_not_sixty_seven_percent() -> None:
    """The gate 3 stop condition. A half-matched settlement posts a WRONG
    journal entry, so there is no partial credit anywhere in scoring."""
    assert not is_correct(frozenset({"A", "B"}), ["A", "B", "C"])


def test_a_superset_is_also_wrong() -> None:
    assert not is_correct(frozenset({"A", "B", "C", "D"}), ["A", "B", "C"])


def test_no_partial_credit_reaches_precision() -> None:
    t = truth({"UTR-1": ["A", "B", "C"]})
    r = RunResult(matches={"UTR-1": match("UTR-1", ["A", "B"])})
    m = score(t, r, settings=SETTINGS)
    assert m.correct == 0
    assert m.match_precision == 0.0, "overlap scoring would have reported 0.67 here"


def test_a_match_claiming_no_ledger_rows_is_unrepresentable() -> None:
    """`set() == set()` is True, so an empty claim against an unmapped credit
    would score as correct and hand out free precision. A match that explains
    nothing is an exception, not a match."""
    with pytest.raises(ValueError, match="claims no ledger rows"):
        match("UTR-ORPHAN", [])
    assert not is_correct(frozenset(), [])


def test_claiming_an_unmapped_credit_is_a_false_positive() -> None:
    t = truth({"UTR-1": ["A"]})
    r = RunResult(matches={"UTR-ORPHAN": match("UTR-ORPHAN", ["ORD-X"])})
    m = score(t, r, settings=SETTINGS)
    assert m.correct == 0
    assert m.false_positives == ("UTR-ORPHAN",)


# ==========================================================================
# Match rate vs match precision — the distinction that wins the argument
# ==========================================================================


def test_team_a_beats_team_b_on_the_number_that_matters() -> None:
    """§7.2, verbatim.

    Team A answers 95 of 100 and gets 94 right.
    Team B answers all 100 and gets 82 right.
    B's dashboard looks better; B's books are wrecked.
    """
    mappings = {f"UTR-{i}": [f"ORD-{i}"] for i in range(100)}
    t = truth(mappings)

    team_a = RunResult(
        matches={
            f"UTR-{i}": match(f"UTR-{i}", [f"ORD-{i}" if i != 94 else "ORD-WRONG"])
            for i in range(95)
        }
    )
    team_b = RunResult(
        matches={
            f"UTR-{i}": match(f"UTR-{i}", [f"ORD-{i}" if i < 82 else "ORD-WRONG"])
            for i in range(100)
        }
    )

    a = score(t, team_a, settings=SETTINGS)
    b = score(t, team_b, settings=SETTINGS)

    assert a.match_rate == 0.95
    assert round(a.match_precision, 3) == 0.989
    assert b.match_rate == 1.00
    assert b.match_precision == 0.82

    assert b.match_rate > a.match_rate          # B's dashboard looks better
    assert a.match_precision > b.match_precision  # A's books are not wrecked


def test_declining_to_answer_costs_rate_but_not_precision() -> None:
    """In finance a wrong answer is worse than "I don't know"."""
    t = truth({f"UTR-{i}": [f"ORD-{i}"] for i in range(10)})
    cautious = RunResult(
        matches={f"UTR-{i}": match(f"UTR-{i}", [f"ORD-{i}"]) for i in range(5)}
    )
    m = score(t, cautious, settings=SETTINGS)
    assert m.match_rate == 0.5
    assert m.match_precision == 1.0


def test_precision_is_zero_not_undefined_when_nothing_is_attempted() -> None:
    m = score(truth({"UTR-1": ["A"]}), RunResult(), settings=SETTINGS)
    assert m.match_rate == 0.0
    assert m.match_precision == 0.0


# ==========================================================================
# Exception recall and self-reported misses
# ==========================================================================


def test_exception_recall_counts_planted_not_reported() -> None:
    t = truth({}, [("ORD-1", "AUTO_REFUNDED"), ("UTR-9", "DUPLICATE_UTR")])
    r = RunResult(
        exceptions=(
            ExceptionOutcome(ref="ORD-1", reason_code=ReasonCode.AUTO_REFUNDED),
        )
    )
    m = score(t, r, settings=SETTINGS)
    assert m.planted == 2
    assert m.caught == 1
    assert m.exception_recall == 0.5


def test_misses_are_named_with_their_type(capsys: pytest.CaptureFixture[str]) -> None:
    """§7.4's self-reported miss line — worth more than a clean 100%."""
    t = truth({}, [("ORD-4471", "ROUNDING_DRIFT")])
    m = score(t, RunResult(), settings=SETTINGS)
    assert m.missed == (("ORD-4471", "ROUNDING_DRIFT"),)


def test_flagging_things_that_were_never_planted_does_not_inflate_recall() -> None:
    t = truth({}, [("ORD-1", "AUTO_REFUNDED")])
    r = RunResult(
        exceptions=tuple(
            ExceptionOutcome(ref=f"NOISE-{i}", reason_code=ReasonCode.AMOUNT_MISMATCH)
            for i in range(50)
        )
    )
    m = score(t, r, settings=SETTINGS)
    assert m.exception_recall == 0.0


# ==========================================================================
# Calibration — the trust argument (§2.5)
# ==========================================================================


def test_calibration_buckets_by_confidence() -> None:
    t = truth({f"UTR-{i}": [f"ORD-{i}"] for i in range(4)})
    r = RunResult(
        matches={
            "UTR-0": match("UTR-0", ["ORD-0"], 0.99),
            "UTR-1": match("UTR-1", ["ORD-1"], 0.90),
            "UTR-2": match("UTR-2", ["ORD-2"], 0.75),
            "UTR-3": match("UTR-3", ["WRONG"], 0.40),
        }
    )
    m = score(t, r, settings=SETTINGS)
    by_label = {b.label: b for b in m.calibration}
    assert by_label["0.95 - 1.00"].count == 1
    assert by_label["0.95 - 1.00"].precision == 1.0
    assert by_label["below 0.70"].count == 1
    assert by_label["below 0.70"].precision == 0.0


def test_auto_post_band_uses_the_configured_threshold() -> None:
    t = truth({"UTR-1": ["A"], "UTR-2": ["B"]})
    r = RunResult(
        matches={
            "UTR-1": match("UTR-1", ["A"], 0.95),  # inclusive lower edge
            "UTR-2": match("UTR-2", ["B"], 0.94),
        }
    )
    m = score(t, r, settings=SETTINGS)
    assert m.auto_posted == 1
    assert m.auto_resolution == 0.5


# ==========================================================================
# The gate 3 stop condition: no peeking at agent internals
# ==========================================================================


def test_eval_never_imports_agent_internals() -> None:
    """§7.3 / gate 3: the eval may read truth.json and the agent's public
    output, and nothing else. Importing a matcher would be grading the exam with
    the answer key visible to the student.
    """
    forbidden = {"matching", "adjudication", "posting", "ingest", "exceptions_"}
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted((root / "eval").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                if mod.split(".")[0] in forbidden:
                    offenders.append(f"{path.name}:{node.lineno} imports {mod}")
    assert not offenders, offenders


def test_eval_touches_the_pipeline_only_through_the_factory() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "eval" / "evaluate.py").read_text(encoding="utf-8"))
    pipeline_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("pipeline")
    }
    assert pipeline_imports <= {"pipeline.factory"}


def test_scoring_needs_only_truth_and_a_run_result() -> None:
    """`score()` is pure: an answer key and a public result, no agent at all."""
    m = score(
        truth({"UTR-1": ["A"]}),
        RunResult(matches={"UTR-1": match("UTR-1", ["A"])}),
        settings=SETTINGS,
    )
    assert m.match_precision == 1.0


# ==========================================================================
# Determinism and versioning
# ==========================================================================


def test_identical_inputs_produce_an_identical_fingerprint() -> None:
    t = truth({"UTR-1": ["A", "B"]})
    r = RunResult(matches={"UTR-1": match("UTR-1", ["A", "B"])})
    a = score(t, r, settings=SETTINGS, elapsed_s=0.1)
    b = score(t, r, settings=SETTINGS, elapsed_s=9.9)
    assert a.fingerprint == b.fingerprint, "timing must not affect the fingerprint"
    assert a.throughput != b.throughput


def test_a_changed_score_changes_the_fingerprint() -> None:
    t = truth({"UTR-1": ["A", "B"]})
    good = score(t, RunResult(matches={"UTR-1": match("UTR-1", ["A", "B"])}), settings=SETTINGS)
    bad = score(t, RunResult(matches={"UTR-1": match("UTR-1", ["A"])}), settings=SETTINGS)
    assert good.fingerprint != bad.fingerprint


def test_stale_generator_version_is_refused(tmp_path: Path) -> None:
    """§6.3: eval refuses a dataset from a different major version rather than
    scoring against an answer key that no longer describes the data."""
    (tmp_path / "truth.json").write_text(
        json.dumps({"generator_version": "9.0.0", "mappings": {}, "exceptions": []}),
        encoding="utf-8",
    )
    with pytest.raises(TruthVersionError, match="regenerate"):
        load_truth(tmp_path)


# ==========================================================================
# End to end against a real dataset — the gate 3 expectation
# ==========================================================================


def test_an_agent_that_answers_nothing_scores_zero() -> None:
    """A harness that cannot report a failing score cannot be trusted to report
    a passing one. Held against an empty result rather than a stub agent, since
    the pipeline became real at gate 5."""
    t = truth(
        {"UTR-1": ["ORD-1"]}, [("ORD-9", "AUTO_REFUNDED")]
    )
    m = score(t, RunResult(), settings=SETTINGS)
    assert m.total > 0
    assert m.planted > 0
    assert m.match_rate == 0.0
    assert m.match_precision == 0.0
    assert m.exception_recall == 0.0
    assert m.llm_calls == 0
    assert len(m.missed) == m.planted


def test_the_real_pipeline_now_matches_and_stays_exact(tmp_path: Path) -> None:
    """Gate 5: L1 is wired in, so the harness scores real work — and every
    claim it makes is exactly right (§4.1)."""
    ds = ensure_dataset(tmp_path / "seed42", 42, 200)
    m = evaluate(ds)
    assert m.attempted > 0
    assert m.match_precision == 1.0
    # NOT `llm_calls == 0`. That held at gate 5 because L4 did not exist; once
    # it did, and once a verdict for this dataset was in the committed cache,
    # the honest number here became 1 — a record genuinely reached L4 and got
    # an answer, without a request being made. Asserting zero would have forced
    # us to either delete real cached verdicts or pretend the layer was idle.
    # The standing invariant is the BUDGET (§2.2), so that is what is asserted.
    assert m.llm_calls / max(m.total, 1) < 0.10


def test_the_deterministic_core_needs_no_adjudicator(tmp_path: Path) -> None:
    """The `--no-llm` claim, asserted rather than assumed (§4.4).

    Separate from the test above because the two say different things: that one
    is about the pipeline being correct, this one is about it being correct
    *without* L4. Conflating them is how "works offline" quietly stops being
    true.
    """
    ds = ensure_dataset(tmp_path / "seed42", 42, 200)
    m = evaluate(ds, no_llm=True)
    assert m.llm_calls == 0
    assert m.llm_cost_paise == 0
    assert m.match_precision == 1.0


def test_a_perfect_oracle_would_score_one_hundred(tmp_path: Path) -> None:
    """Prove the harness can report success, not just failure — otherwise a 0%
    stub tells you nothing about whether the scoring works."""
    ds = ensure_dataset(tmp_path / "seed42", 42, 200)
    t = load_truth(ds)
    oracle = RunResult(
        matches={
            utr: match(utr, members, 0.99) for utr, members in t["mappings"].items()
        },
        exceptions=tuple(
            ExceptionOutcome(ref=e["ref"], reason_code=ReasonCode(e["type"]))
            for e in t["exceptions"]
        ),
    )
    m = score(t, oracle, settings=SETTINGS)
    assert m.match_rate == 1.0
    assert m.match_precision == 1.0
    assert m.exception_recall == 1.0
    assert m.auto_resolution == 1.0
    assert m.missed == ()


def test_dataset_is_regenerated_on_a_scale_mismatch(tmp_path: Path) -> None:
    """`data/seedN` is reused across scales, so scoring whatever happens to be
    on disk would silently report the wrong denominator."""
    ds = tmp_path / "seed42"
    ensure_dataset(ds, 42, 100)
    assert load_truth(ds)["scale"] == 100
    ensure_dataset(ds, 42, 300)
    assert load_truth(ds)["scale"] == 300


def test_no_regenerate_scores_what_is_on_disk(tmp_path: Path) -> None:
    ds = tmp_path / "seed42"
    ensure_dataset(ds, 42, 100)
    ensure_dataset(ds, 42, 300, regenerate=False)
    assert load_truth(ds)["scale"] == 100
