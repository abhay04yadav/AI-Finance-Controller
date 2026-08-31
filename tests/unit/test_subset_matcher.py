"""L3 — N:1 candidate generation, against real datasets. Guide §4.3.

The solver is tested with plain integers in `test_subset_solver.py`. This file
tests the payments knowledge: which rows are eligible, how the fee model turns a
credit into a target, and what happens when arithmetic cannot decide.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import statistics
import time
from pathlib import Path

import pytest

from core.config import Settings
from core.dates import BusinessCalendar
from core.models import Direction, Source
from core.reason_codes import ReasonCode
from eval.evaluate import build_pipeline, evaluate, is_correct
from generator.generate import generate
from ingest.normalizer import load_dataset
from matching.exact_matcher import ExactMatcher
from matching.fee_model import FeeModel
from matching.protocols import MatchContext
from matching.subset_matcher import NAME as L3_NAME
from matching.subset_matcher import SubsetMatcher

SCALE = 500


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("l3")
    generate(42, SCALE, out)
    return out


@pytest.fixture(scope="module")
def truth(dataset: Path) -> dict:
    return json.loads((dataset / "truth.json").read_text(encoding="utf-8"))


def after_l1(dataset: Path) -> MatchContext:
    """A context with L1 done and the fee model derived — L3's real input."""
    loaded = load_dataset(dataset)
    ctx = MatchContext.build(
        loaded.records, calendar=BusinessCalendar(), settings=Settings()
    )
    for proposal in ExactMatcher().propose(ctx):
        ctx.accept(proposal)
    ctx.refresh_derived()
    return ctx


# ==========================================================================
# The §4.3 verification block
# ==========================================================================


def test_l3_window_prunes_hard(dataset: Path) -> None:
    """§4.3: mean candidate pool under ~12 rows, else the window is too loose.

    This is §2.4 made measurable. A five-business-day span holds ~40 orders at
    this volume; the back-solved capture date holds one day's worth.
    """
    ctx = after_l1(dataset)
    matcher = SubsetMatcher()
    pools = [
        len(matcher._pool(ctx, credit, widen=0))
        for credit in ctx.open_bank_credits()
    ]
    assert pools
    assert statistics.fmean(pools) < 12, (
        f"mean pool {statistics.fmean(pools):.1f} — the window is too loose"
    )


def test_l3_never_exceeds_budget(dataset: Path) -> None:
    """§4.3: no single credit takes over 100 ms."""
    ctx = after_l1(dataset)
    matcher = SubsetMatcher()
    timings = []
    for credit in ctx.open_bank_credits():
        start = time.perf_counter()
        matcher._explain(ctx, credit, ctx.fee_model)
        timings.append((time.perf_counter() - start) * 1000)
    assert max(timings) < 100, f"slowest credit took {max(timings):.0f} ms"


def test_l3_finds_refund_combinations(dataset: Path, truth: dict) -> None:
    """§4.3: a cross-period refund must be found alongside the orders."""
    ctx = after_l1(dataset)
    proposals = SubsetMatcher().propose(ctx)
    refunds_used = {
        oid
        for p in proposals
        for oid in p.ledger_ids
        if oid.startswith("RFND-")
    }
    planted = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "CROSS_PERIOD_REFUND"
    }
    assert planted
    assert refunds_used & planted, "L3 solved no cross-period refund"


# ==========================================================================
# Precision — L3 may decline, but must not be wrong
# ==========================================================================


@pytest.mark.parametrize("seed", [42, 7, 99])
def test_l3_precision_is_perfect(tmp_path: Path, seed: int) -> None:
    """L3 is allowed to be uncertain. It is not allowed to be wrong."""
    out = tmp_path / f"s{seed}"
    generate(seed, SCALE, out)
    t = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    ctx = after_l1(out)
    wrong = [
        p
        for p in SubsetMatcher().propose(ctx)
        if not is_correct(p.ledger_ids, t["mappings"].get(p.bank_utr, []))
    ]
    assert not wrong, f"seed {seed}: {[p.bank_utr for p in wrong]}"


@pytest.mark.parametrize("seed", [42, 7, 99])
def test_l3_lifts_the_match_rate_without_costing_precision(
    tmp_path: Path, seed: int
) -> None:
    out = tmp_path / f"e{seed}"
    generate(seed, SCALE, out)
    m = evaluate(out)
    l3 = next((s for s in m.by_strategy if s.name == L3_NAME), None)
    assert l3 is not None and l3.attempted > 0
    assert l3.precision == 1.0
    assert m.match_precision == 1.0
    assert m.match_rate > 0.85


def test_no_ledger_row_is_used_twice(dataset: Path) -> None:
    """Two credits explained by the same order would double-count revenue."""
    ctx = after_l1(dataset)
    used: list[str] = []
    for p in SubsetMatcher().propose(ctx):
        used.extend(p.ledger_ids)
    assert len(used) == len(set(used))


def test_l3_never_touches_a_credit_l1_already_claimed(dataset: Path) -> None:
    """Each layer consumes the residual of the last (§5.4)."""
    ctx = after_l1(dataset)
    claimed = {p.bank_utr for p in ctx.accepted}
    assert not {p.bank_utr for p in SubsetMatcher().propose(ctx)} & claimed


# ==========================================================================
# Refunds flow through the same path (§4.3a)
# ==========================================================================


def test_there_is_no_is_refund_branch(dataset: Path) -> None:
    """The gate 7 stop condition.

    Direction appears in eligibility (which window a row belongs to) and in the
    gross-equivalent transform, both of which §4.3b requires. What must not
    exist is a separate matching path for refunds.
    """
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "drift_check", root / "scripts" / "drift_check.py"
    )
    assert spec and spec.loader
    drift = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drift)

    # Code only. The module docstring says "no `if is_refund:` anywhere below",
    # and a naive text scan cannot tell describing a rule from breaking it.
    hits = drift.scan(
        [root / "matching" / "subset_matcher.py"], r"is_refund|is_a_refund"
    )
    assert not hits, f"a refund-specific branch exists: {hits}"


def test_refunds_enter_as_negative_numbers(dataset: Path) -> None:
    ctx = after_l1(dataset)
    matcher = SubsetMatcher()
    credit = next(
        c
        for c in ctx.open_bank_credits()
        if any(
            r.direction is Direction.OUTFLOW
            for r in matcher._pool(ctx, c, widen=0)
        )
    )
    pool = matcher._pool(ctx, credit, widen=0)
    from matching.subset_matcher import _gross_equivalents

    values = _gross_equivalents(pool, ctx.fee_model)
    assert any(v < 0 for v in values), "no refund reached the solver as a negative"


def test_a_refund_is_worth_more_than_its_face_value_in_gross_space() -> None:
    """A refund is deducted from the payout AFTER the MDR, so inverting the
    credit scales it up. Using face value left every cross-period refund ~2%
    short — too small to look wrong, too large to fall inside tolerance."""
    fee = FeeModel(rate=0.0183)
    face = 24_000
    assert fee.expected_gross(face) > face
    assert abs(fee.expected_gross(face) - 24_530) < 50


def test_the_stated_drift_is_the_one_the_solver_actually_enforced(
    dataset: Path,
) -> None:
    """A reason that says "within N paise of tolerance T" must have N <= T.

    `_propose` measured the drift over face values while the solver matched
    over gross equivalents, so on any settlement carrying a refund the two
    diverged by the refund's own MDR and GST. Three of eleven L3 proposals on
    seed 42 printed a drift above their own tolerance, the worst claiming
    "within 717 paise of rounding tolerance 0" about a reconstruction that
    tied exactly. The match was right every time; only the sentence explaining
    it was wrong, which is the more dangerous of the two — a controller who
    checks that arithmetic and finds it absurd stops trusting the lines that
    are correct.
    """
    result = build_pipeline().run(dataset)

    stated = re.compile(r"within (\d+) paise of rounding tolerance (\d+)")
    checked = 0
    for item in result.review_queue:
        found = stated.search(item.reason)
        if not found:
            continue
        checked += 1
        drift, tolerance = int(found.group(1)), int(found.group(2))
        assert drift <= tolerance, (
            f"{item.utr} says it is within {drift} paise of a {tolerance} "
            f"paise tolerance, which is not a thing that can be true"
        )

    assert checked, "no L3 proposal quoted a drift — the test proved nothing"


# ==========================================================================
# The window asymmetry (§4.3b)
# ==========================================================================


def test_the_refund_window_is_wider_than_the_order_window(dataset: Path) -> None:
    """The whole trick for CROSS_PERIOD_REFUND. If both windows matched, that
    class would be unsolvable by construction."""
    ctx = after_l1(dataset)
    matcher = SubsetMatcher()
    credit = ctx.open_bank_credits()[0]
    pool = matcher._pool(ctx, credit, widen=0)

    orders = [r for r in pool if r.direction is Direction.INFLOW]
    refunds = [r for r in pool if r.direction is Direction.OUTFLOW]
    if orders and refunds:
        order_span = max(r.value_date for r in orders) - min(
            r.value_date for r in orders
        )
        refund_span = max(r.value_date for r in refunds) - min(
            r.value_date for r in refunds
        )
        assert refund_span >= order_span


def test_the_order_window_is_the_back_solved_capture_date(dataset: Path) -> None:
    """Exact, not approximate: add and subtract business days are inverses, so
    a settlement pushed off a Sunday lands back on its true capture date."""
    ctx = after_l1(dataset)
    matcher = SubsetMatcher()
    credit = ctx.open_bank_credits()[0]
    expected = ctx.calendar.subtract_business_days(
        credit.value_date, ctx.settings.settlement_days
    )
    pool = matcher._pool(ctx, credit, widen=0)
    orders = [r for r in pool if r.direction is Direction.INFLOW]
    assert orders
    assert all(r.value_date == expected for r in orders)


# ==========================================================================
# Ambiguity and failure are reported, never guessed (§4.3d)
# ==========================================================================


def test_ambiguous_credits_are_recorded_for_adjudication(dataset: Path) -> None:
    """Multiple solutions is the signal that routes a case to L4, not a
    nuisance to be resolved by picking one."""
    ctx = after_l1(dataset)
    SubsetMatcher().propose(ctx)
    for ambiguity in ctx.ambiguities:
        assert len(ambiguity.options) > 1
        assert ambiguity.credit_paise > 0
        assert ambiguity.target_paise > 0


def test_an_unexplained_credit_becomes_an_exception(dataset: Path) -> None:
    ctx = after_l1(dataset)
    SubsetMatcher().propose(ctx)
    mine = [f for f in ctx.flags if f.raised_by == L3_NAME]
    assert mine
    for flag in mine:
        assert flag.what and flag.why
        assert flag.amount_paise is not None
        assert flag.reason_code in (
            ReasonCode.AMOUNT_MISMATCH,
            # "several answers, nobody asked" — never ADJUDICATION_REJECTED,
            # which means a verdict was given and a guardrail threw it out.
            ReasonCode.AMBIGUOUS_UNADJUDICATED,
        )


def test_every_proposal_explains_itself(dataset: Path) -> None:
    """§2.7 rule 4: no automated decision without a justification."""
    ctx = after_l1(dataset)
    for p in SubsetMatcher().propose(ctx):
        assert "order(s)" in p.reason
        assert "MDR" in p.reason
        assert "inferred_fee_rate" in p.evidence
        assert p.strategy == L3_NAME


def test_confidence_never_reaches_l1s_certainty(dataset: Path) -> None:
    """L3 infers a target from a learned rate; L1 joins on identifiers. Only one
    of those is certain."""
    ctx = after_l1(dataset)
    for p in SubsetMatcher().propose(ctx):
        assert p.confidence < 1.0
        assert p.confidence <= 0.92


# ==========================================================================
# The LOW_CONFIDENCE contract (written at gate 6, enforced here)
# ==========================================================================


def test_a_guessed_rate_never_produces_an_auto_postable_match(dataset: Path) -> None:
    ctx = after_l1(dataset)
    ctx.fee_model = FeeModel.disabled()
    settings = Settings()
    for p in SubsetMatcher().propose(ctx):
        assert p.confidence < settings.auto_post_threshold


def test_l3_asks_the_fee_model_for_its_tolerance() -> None:
    from matching import subset_matcher

    assert "tolerance_paise" in inspect.getsource(subset_matcher)


# ==========================================================================
# Determinism and layering
# ==========================================================================


def test_l3_is_deterministic(dataset: Path) -> None:
    a = [(p.bank_utr, sorted(p.ledger_ids)) for p in SubsetMatcher().propose(after_l1(dataset))]
    b = [(p.bank_utr, sorted(p.ledger_ids)) for p in SubsetMatcher().propose(after_l1(dataset))]
    assert a == b


def test_l3_returns_an_empty_list_when_it_has_no_fee_model() -> None:
    """§5.4, Liskov: no opinion is [], never None, never an exception."""
    ctx = MatchContext.build((), calendar=BusinessCalendar(), settings=Settings())
    ctx.fee_model = None
    assert SubsetMatcher().propose(ctx) == []


def test_the_matcher_never_reads_the_answer_key() -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "drift_check", root / "scripts" / "drift_check.py"
    )
    assert spec and spec.loader
    drift = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drift)

    # Code only: the docstrings discuss keeping the exception page "truthful",
    # and describing a rule is not breaking it.
    assert not drift.scan([root / "matching" / "subset_matcher.py"], r"truth")

    from matching import subset_matcher

    src = inspect.getsource(subset_matcher)
    tree = ast.parse(src)
    imported = {
        n.module.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not imported & {"eval", "generator"}


def test_credits_the_answer_key_does_not_map_are_not_claimed(
    dataset: Path, truth: dict
) -> None:
    """MISSING_IN_LEDGER orphans have no ledger rows to be explained by.
    Claiming one would be a false positive."""
    ctx = after_l1(dataset)
    orphans = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "MISSING_IN_LEDGER"
    }
    assert orphans
    assert not {p.bank_utr for p in SubsetMatcher().propose(ctx)} & orphans


def test_settlement_records_are_never_used_as_ledger_rows(dataset: Path) -> None:
    ctx = after_l1(dataset)
    settlement_ids = {r.external_id for r in ctx.records if r.source is Source.SETTLEMENT}
    for p in SubsetMatcher().propose(ctx):
        assert not p.ledger_ids & settlement_ids
