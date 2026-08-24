"""L1 — the deterministic matcher. Guide §4.1.

**The single most important test file in the repo.**

L1 declares confidence 1.00. If a confidence-1.00 match can be wrong, then every
confidence below it means nothing, the calibration table in §2.5 is fiction, and
the strongest claim in the demo — "441 auto-posted, zero errors" — is false.

So precision here is not a target, it is a requirement: exactly 100%, on every
seed, or the gate fails.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from core.config import Settings
from core.dates import BusinessCalendar
from core.models import Source
from eval.evaluate import evaluate, is_correct
from generator.generate import generate
from ingest.normalizer import load_dataset
from matching.exact_matcher import NAME as L1_NAME
from matching.exact_matcher import ExactMatcher
from matching.protocols import MatchContext

SEEDS = (42, 7, 99)
SCALE = 500


def build(dataset: Path) -> tuple[MatchContext, list]:
    """Run L1 alone over a dataset and return the context plus its proposals."""
    loaded = load_dataset(dataset)
    ctx = MatchContext.build(
        loaded.records, calendar=BusinessCalendar(), settings=Settings()
    )
    return ctx, ExactMatcher().propose(ctx)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("l1")
    generate(42, SCALE, out)
    return out


@pytest.fixture(scope="module")
def truth(dataset: Path) -> dict:
    return json.loads((dataset / "truth.json").read_text(encoding="utf-8"))


# ==========================================================================
# THE gate. Exactly 100%, on every seed.
# ==========================================================================


@pytest.mark.parametrize("seed", SEEDS)
def test_l1_precision_is_perfect(tmp_path: Path, seed: int) -> None:
    """§4.1: L1 claims confidence 1.00 — it must never be wrong, on any dataset.

    Checked across three seeds, because a matcher that is right on one dataset
    and wrong on another is not deterministic, it is lucky.
    """
    out = tmp_path / f"seed{seed}"
    generate(seed, SCALE, out)
    t = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    _, proposals = build(out)

    assert proposals, "L1 matched nothing at all"
    wrong = [
        p
        for p in proposals
        if not is_correct(p.ledger_ids, t["mappings"].get(p.bank_utr, []))
    ]
    assert not wrong, (
        f"seed {seed}: {len(wrong)} confidence-1.00 matches are wrong — "
        f"first offender {wrong[0].bank_utr}: claimed "
        f"{sorted(wrong[0].ledger_ids)[:4]}, truth wants "
        f"{sorted(t['mappings'].get(wrong[0].bank_utr, []))[:4]}"
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_l1_coverage_is_reasonable(tmp_path: Path, seed: int) -> None:
    """§4.1: 60-90%.

    Below 60% the settlement report is not doing its job as the bridge. Above
    90% the problem is too easy — either order IDs leaked into the bank
    narration, or the report covers every credit and L3 has nothing to solve.
    """
    out = tmp_path / f"cov{seed}"
    generate(seed, SCALE, out)
    t = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    _, proposals = build(out)

    coverage = len(proposals) / len(t["mappings"])
    assert 0.60 <= coverage <= 0.90, f"seed {seed}: L1 coverage {coverage:.1%}"


@pytest.mark.parametrize("seed", SEEDS)
def test_l1_leaves_real_work_for_later_layers(tmp_path: Path, seed: int) -> None:
    """§3.1 budgets L1 at ~70-80%. If it resolves nearly everything, the N:1
    subset matcher is never exercised and the hard part is untested."""
    out = tmp_path / f"res{seed}"
    generate(seed, SCALE, out)
    t = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    _, proposals = build(out)
    residual = len(t["mappings"]) - len(proposals)
    assert residual >= 5, f"seed {seed}: only {residual} credits left for L3"


# ==========================================================================
# Duplicate UTRs: refuse, never pick one
# ==========================================================================


def test_l1_never_consumes_a_duplicate_utr(dataset: Path, truth: dict) -> None:
    """§4.1 step 5. Matching either copy double-posts revenue."""
    duplicates = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "DUPLICATE_UTR"
    }
    assert duplicates, "the dataset planted no duplicates, so this proves nothing"
    _, proposals = build(dataset)
    assert not [p for p in proposals if p.bank_utr in duplicates]


def test_l1_flags_every_duplicate_it_refuses(dataset: Path, truth: dict) -> None:
    """Refusing silently would leave real money unexplained and unreported."""
    duplicates = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "DUPLICATE_UTR"
    }
    ctx, _ = build(dataset)
    flagged = {f.ref for f in ctx.flags if f.reason_code.value == "DUPLICATE_UTR"}
    assert duplicates <= flagged


def test_a_duplicate_flag_explains_itself(dataset: Path) -> None:
    """§2.7 rule 4 and §8.2: WHAT and WHY, in a controller's language."""
    ctx, _ = build(dataset)
    dupes = [f for f in ctx.flags if f.reason_code.value == "DUPLICATE_UTR"]
    assert dupes
    flag = dupes[0]
    assert "appears" in flag.what
    assert "twice" in flag.why or "one UTR per settlement" in flag.why
    assert flag.amount_paise is not None
    assert flag.raised_by == L1_NAME


# ==========================================================================
# The condition §4.1 omits, and why it is load-bearing
# ==========================================================================


def test_l1_refuses_a_settlement_with_an_unitemised_deduction(
    dataset: Path, truth: dict
) -> None:
    """A cross-period refund is netted out of the payout but never itemised
    among the order rows, so `Σ ledger gross == settlement gross` still holds.
    §4.1's four steps would match it and claim the wrong member set at
    confidence 1.00. The shortfall is the tell."""
    loaded = load_dataset(dataset)
    shortfall_utrs = {
        r.settlement().utr
        for r in loaded.by_source(Source.SETTLEMENT)
        if r.settlement().unitemised_paise != 0
    }
    assert shortfall_utrs, "no cross-period refunds present, so this proves nothing"

    _, proposals = build(dataset)
    claimed = {p.bank_utr for p in proposals}
    assert not (claimed & shortfall_utrs)


def test_those_settlements_would_have_been_wrong_if_matched(
    dataset: Path, truth: dict
) -> None:
    """Prove the refusal is necessary, not merely cautious."""
    loaded = load_dataset(dataset)
    wrong_if_claimed = 0
    for rec in loaded.by_source(Source.SETTLEMENT):
        d = rec.settlement()
        if d.unitemised_paise == 0:
            continue
        want = truth["mappings"].get(d.utr)
        if want and not is_correct(frozenset(d.order_ids), want):
            wrong_if_claimed += 1
    assert wrong_if_claimed > 0, (
        "matching these would have been harmless, so the extra condition is "
        "unnecessary — check the generator still plants cross-period refunds"
    )


# ==========================================================================
# The other refusals
# ==========================================================================


def test_l1_refuses_when_an_order_is_missing_from_the_ledger(
    dataset: Path, truth: dict
) -> None:
    missing = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "MISSING_IN_LEDGER"
    }
    assert missing
    ctx, proposals = build(dataset)
    assert not {p.bank_utr for p in proposals} & missing
    flagged = {f.ref for f in ctx.flags if f.reason_code.value == "MISSING_IN_LEDGER"}
    assert missing <= flagged


def test_l1_ignores_credits_the_report_does_not_cover(dataset: Path) -> None:
    """No settlement row means no bridge and no join key — the genuine N:1 case
    of §1.4. L1 says nothing about them; L3 has to solve them."""
    loaded = load_dataset(dataset)
    bridged = {r.settlement().utr for r in loaded.by_source(Source.SETTLEMENT)}
    all_utrs = {r.external_id for r in loaded.by_source(Source.BANK)}
    unbridged = all_utrs - bridged
    assert unbridged, "the report covers everything, so L3 has nothing to do"

    _, proposals = build(dataset)
    assert not {p.bank_utr for p in proposals} & unbridged


def test_l1_matches_a_late_authorization_despite_a_failed_ledger_status(
    dataset: Path, truth: dict
) -> None:
    """The money really did arrive; only the ledger status is stale (§1.3).
    L1 joins on identity, not on status, so it should resolve these."""
    late = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "LATE_AUTHORIZATION"
    }
    assert late
    _, proposals = build(dataset)
    claimed_orders = {oid for p in proposals for oid in p.ledger_ids}
    assert late & claimed_orders, "L1 resolved none of the late authorizations"


def test_l1_matches_across_a_holiday_shift(dataset: Path, truth: dict) -> None:
    """The join is by identifier, so a shifted settle date is irrelevant to L1.
    It is L3, matching on a date window, that has to care (§1.5)."""
    shifted = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "HOLIDAY_SHIFT"
    }
    assert shifted
    _, proposals = build(dataset)
    matched = {p.bank_utr for p in proposals}
    assert shifted & matched


# ==========================================================================
# Contract
# ==========================================================================


def test_every_match_is_confidence_one(dataset: Path) -> None:
    _, proposals = build(dataset)
    assert all(p.confidence == 1.00 for p in proposals)


def test_every_match_carries_a_reason_and_evidence(dataset: Path) -> None:
    """§2.7 rule 4: no automated decision without a justification."""
    _, proposals = build(dataset)
    for p in proposals:
        assert p.settlement_id and p.settlement_id in p.reason
        assert p.bank_utr in p.reason
        assert set(p.evidence) >= {"settlement_id", "utr", "order_id"}
        assert p.strategy == L1_NAME


def test_no_credit_is_claimed_twice(dataset: Path) -> None:
    _, proposals = build(dataset)
    counts = Counter(p.bank_utr for p in proposals)
    assert not [u for u, n in counts.items() if n > 1]


def test_no_ledger_row_is_used_by_two_matches(dataset: Path) -> None:
    """Two credits explained by the same order would double-count revenue."""
    _, proposals = build(dataset)
    used = Counter(oid for p in proposals for oid in p.ledger_ids)
    assert not [o for o, n in used.items() if n > 1]


def test_l1_is_deterministic(dataset: Path) -> None:
    a = [(p.bank_utr, sorted(p.ledger_ids)) for p in build(dataset)[1]]
    b = [(p.bank_utr, sorted(p.ledger_ids)) for p in build(dataset)[1]]
    assert a == b


def test_l1_returns_an_empty_list_rather_than_none_on_an_empty_dataset(
    tmp_path: Path,
) -> None:
    """§5.4, Liskov: a strategy with no opinion returns [], never None."""
    ctx = MatchContext.build((), calendar=BusinessCalendar(), settings=Settings())
    assert ExactMatcher().propose(ctx) == []


# ==========================================================================
# End to end, through the eval harness
# ==========================================================================


@pytest.mark.parametrize("seed", SEEDS)
def test_end_to_end_l1_precision_through_the_harness(
    tmp_path: Path, seed: int
) -> None:
    """The number the Review Guide greps for, computed the way the eval does."""
    out = tmp_path / f"e2e{seed}"
    generate(seed, SCALE, out)
    m = evaluate(out)

    l1 = next(s for s in m.by_strategy if s.name == L1_NAME)
    assert l1.precision == 1.0, f"seed {seed}: L1 precision {l1.precision:.4%}"
    assert m.match_precision == 1.0

    # The 60-90% band is L1's COVERAGE, not the pipeline's match rate. Once L3
    # lands the overall rate climbs past 90% — which is the point of having L3,
    # not a sign that L1 got too easy.
    l1_coverage = l1.attempted / m.total
    assert 0.60 <= l1_coverage <= 0.90, f"seed {seed}: L1 coverage {l1_coverage:.1%}"
    assert m.match_rate >= l1_coverage


def test_auto_post_band_is_perfect(dataset: Path) -> None:
    """§4.5: everything posted without asking a human must be correct."""
    m = evaluate(dataset)
    top = next(b for b in m.calibration if b.label.startswith("0.95"))
    assert top.count > 0
    assert top.precision == 1.0


def test_the_answer_key_is_never_read_by_the_matcher() -> None:
    """L1 must not be able to see truth.json, or its precision means nothing."""
    import ast
    import inspect

    from matching import exact_matcher

    src = inspect.getsource(exact_matcher)
    assert "truth" not in src.lower()
    tree = ast.parse(src)
    imported = {
        n.module.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "eval" not in imported
    assert "generator" not in imported
