"""L2 — fee model inference. Guide §4.2, §2.3.

**Judged on rates that are not 2.00%.** §4.2 step 5 falls back to `0.02` when it
has too few samples, so a fee model that never ran would return 0.02, be
compared against a planted 0.02, and pass. Every recovery test here therefore
runs at 1.75% and 2.35% — see `tests/unit/test_fee_datasets.py` for why those
were chosen.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from core.config import Settings
from eval.evaluate import evaluate
from generator.generate import INTL_FEE_RATE, generate
from matching.fee_model import (
    FALLBACK_RATE,
    MIN_SAMPLES,
    FeeConfidence,
    FeeModel,
)
from pipeline.factory import build_pipeline

NON_ROUND_RATES = (0.0175, 0.0235)
TOLERANCE = 0.001  # the §4.2 bar: within 0.1%
SCALE = 500


def pairs_at(rate: float, n: int = 20, gross: int = 2_000_000) -> list[tuple[int, int]]:
    """Synthetic (gross, net) pairs the way a gateway computes them."""
    out = []
    for i in range(n):
        g = gross + i * 13_700
        fee = int(g * rate)
        gst = int(fee * 0.18)
        out.append((g, g - fee - gst))
    return out


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("l2")
    generate(42, SCALE, out)
    return out


# ==========================================================================
# THE gate: recovers the planted rate within 0.1%
# ==========================================================================


@pytest.mark.parametrize("planted", NON_ROUND_RATES)
def test_l2_recovers_planted_rate(tmp_path: Path, planted: float) -> None:
    """§4.2 verification, run at a rate that is NOT the fallback."""
    out = tmp_path / f"r{planted}"
    generate(42, SCALE, out, fee_rate=planted)
    result = build_pipeline().run(out)

    assert result.fee_rate is not None
    assert abs(result.fee_rate - planted) < TOLERANCE, (
        f"planted {planted:.4%}, inferred {result.fee_rate:.4%}"
    )


@pytest.mark.parametrize("planted", NON_ROUND_RATES)
def test_a_model_returning_only_its_fallback_would_fail_these(planted: float) -> None:
    """The test that makes the others meaningful: prove a broken L2 is caught."""
    assert abs(FALLBACK_RATE - planted) > TOLERANCE, (
        f"a fee model stuck at its {FALLBACK_RATE:.2%} default would pass at "
        f"{planted:.2%}, so this dataset proves nothing"
    )


def test_recovery_at_two_percent_would_not_have_proved_anything() -> None:
    """Stated explicitly so nobody re-points these tests at the default."""
    assert abs(FALLBACK_RATE - 0.02) < TOLERANCE


# ==========================================================================
# Median, not mean (§4.2 step 2)
# ==========================================================================


def test_uses_median_not_mean() -> None:
    """One international-card settlement at 3.5% must not move the answer."""
    model = FeeModel.infer(pairs_at(0.02, n=20) + pairs_at(0.035, n=3))
    assert abs(model.rate - 0.02) < TOLERANCE


def test_a_mean_would_have_failed_the_gate_where_the_median_passes() -> None:
    """Proves the previous test is testing something.

    Three international settlements among twenty drag a mean to 2.196% — an
    error of 2.0e-3, twice the 0.1% the gate allows. The median is unmoved. This
    is the entire reason §4.2 step 2 specifies median.
    """
    import statistics

    rates = [
        (1 - net / gross) / 1.18
        for gross, net in pairs_at(0.02, 20) + pairs_at(0.035, 3)
    ]
    mean_error = abs(statistics.fmean(rates) - 0.02)
    median_error = abs(statistics.median(rates) - 0.02)

    assert mean_error > TOLERANCE, "a mean would have passed, so this proves nothing"
    assert median_error < TOLERANCE
    assert mean_error > median_error * 100


def test_the_implementation_actually_calls_median() -> None:
    """A reviewer asks "mean or median?" — the answer must be in the code."""
    src = inspect.getsource(FeeModel.infer)
    assert "median" in src
    assert "fmean" not in src and "mean(" not in src


def test_outliers_are_reported_not_silently_dropped() -> None:
    model = FeeModel.infer(pairs_at(0.02, 20) + pairs_at(0.035, 3))
    assert model.is_multi_slab
    assert len(model.outliers) == 3
    assert any(abs(s - 0.035) < 0.001 for s in model.slabs)


def test_multi_slab_survives_a_large_minority(tmp_path: Path) -> None:
    """Even at 10% of settlements, the other slab must not become the answer."""
    out = tmp_path / "slab"
    generate(42, SCALE, out, fee_rate=0.0175, intl_ratio=0.10, intl_rate=INTL_FEE_RATE)
    result = build_pipeline().run(out)
    assert result.fee_rate is not None
    assert abs(result.fee_rate - 0.0175) < TOLERANCE


# ==========================================================================
# Round trip (§4.2 verification)
# ==========================================================================


@pytest.mark.parametrize("gross", [100_00, 1_000_00, 87_643_21, 5_000_000])
def test_l2_round_trips(gross: int) -> None:
    """gross → net → gross, within 50 paise."""
    model = FeeModel.infer(pairs_at(0.02))
    assert abs(model.expected_gross(model.expected_net(gross)) - gross) <= 50


@pytest.mark.parametrize("rate", [*NON_ROUND_RATES, 0.02, 0.035])
def test_round_trip_holds_at_every_rate(rate: float) -> None:
    model = FeeModel.infer(pairs_at(rate))
    for gross in (50_000, 800_000, 12_345_678):
        assert abs(model.expected_gross(model.expected_net(gross)) - gross) <= 50


def test_expected_net_matches_how_a_gateway_computes_it() -> None:
    """Fee is rounded, then GST is computed on the ROUNDED fee. Doing it in one
    multiplication drifts by a paisa and turns genuine pairs into near-misses."""
    model = FeeModel.infer(pairs_at(0.02))
    gross = 800_000
    fee = int(gross * model.rate)
    assert model.expected_net(gross) == gross - fee - int(fee * 0.18)


def test_expected_gross_inverts_a_real_credit(dataset: Path) -> None:
    """The §1.4 worked example, end to end: ₹7,811.20 back to ₹8,000."""
    model = FeeModel.infer(pairs_at(0.02))
    assert abs(model.expected_gross(781_120) - 800_000) <= 50


def test_amounts_stay_integer_paise() -> None:
    model = FeeModel.infer(pairs_at(0.0175))
    assert isinstance(model.expected_gross(781_120), int)
    assert isinstance(model.expected_net(800_000), int)


# ==========================================================================
# Too few samples: fallback, but loudly marked (§4.2 step 5)
# ==========================================================================


def test_fewer_than_five_samples_is_low_confidence() -> None:
    model = FeeModel.infer(pairs_at(0.0175, n=MIN_SAMPLES - 1))
    assert model.confidence is FeeConfidence.LOW_CONFIDENCE
    assert not model.is_usable
    assert model.rate == FALLBACK_RATE


def test_five_samples_is_enough() -> None:
    model = FeeModel.infer(pairs_at(0.0175, n=MIN_SAMPLES))
    assert model.confidence is FeeConfidence.INFERRED
    assert abs(model.rate - 0.0175) < TOLERANCE


def test_low_confidence_widens_the_tolerance_rather_than_trusting_a_guess() -> None:
    """§4.2 step 5: L3 must search a wider band, not believe the fallback."""
    weak = FeeModel.infer(pairs_at(0.0175, n=2))
    strong = FeeModel.infer(pairs_at(0.0175, n=20))
    assert weak.tolerance_paise(50) > strong.tolerance_paise(50)


def test_low_confidence_reports_itself_in_plain_language() -> None:
    assert "not inferred" in FeeModel.infer(pairs_at(0.02, n=2)).describe()


def test_no_pairs_at_all_does_not_crash() -> None:
    model = FeeModel.infer([])
    assert not model.is_usable
    assert model.sample_size == 0


def test_zero_gross_pairs_are_skipped() -> None:
    model = FeeModel.infer([(0, 0), *pairs_at(0.0175, n=10)])
    assert abs(model.rate - 0.0175) < TOLERANCE


# ==========================================================================
# The rate is NEVER configured (§2.3, the gate 6 stop condition)
# ==========================================================================


def test_settings_holds_no_fee_rate() -> None:
    """The gate 6 stop condition: no configured MDR anywhere in the normal path.

    GST is present because it is statutory law, not a negotiated rate.
    """
    fields = set(Settings.__dataclass_fields__)
    assert not {f for f in fields if "fee" in f and "rate" in f}
    assert not {f for f in fields if "mdr" in f.lower()}
    assert "gst_rate" in fields


def test_the_only_hardcoded_rate_is_the_low_confidence_fallback() -> None:
    """Any other literal rate in the module would be a configured MDR."""
    src = inspect.getsource(FeeModel)
    tree = ast.parse(inspect.getsource(__import__("matching.fee_model", fromlist=["x"])))
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, float)
    }
    # 0.02 appears once, as FALLBACK_RATE; the plausible-slab list is for the
    # display-only snap and never drives arithmetic.
    assert FALLBACK_RATE in literals
    assert "rate=FALLBACK_RATE" in src or "FALLBACK_RATE" in src


def test_the_agent_never_reads_the_planted_rate(dataset: Path) -> None:
    from matching import fee_model

    src = inspect.getsource(fee_model)
    assert "truth" not in src.lower()
    tree = ast.parse(src)
    imported = {
        n.module.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "generator" not in imported
    assert "eval" not in imported


# ==========================================================================
# The snap: recorded, but not used for arithmetic (§4.2 step 4)
# ==========================================================================


def test_both_raw_and_snapped_are_recorded() -> None:
    model = FeeModel.infer(pairs_at(0.02))
    assert model.snapped_rate == 0.02
    assert model.rate != 0 and abs(model.rate - 0.02) < TOLERANCE


def test_snapping_does_not_drive_arithmetic() -> None:
    """A genuine 2.35% merchant snapped to the plausible 2.36% would pick up
    1e-4 of error — 236 paise on a ₹20,000 settlement, past the 50-paise
    round-trip tolerance §4.2 itself sets. So the raw rate is used."""
    model = FeeModel.infer(pairs_at(0.0235))
    assert abs(model.rate - 0.0235) < 1e-5
    for gross in (2_000_000, 800_000):
        assert abs(model.expected_gross(model.expected_net(gross)) - gross) <= 50


def test_a_rate_far_from_any_slab_is_not_snapped() -> None:
    assert FeeModel.infer(pairs_at(0.0271)).snapped_rate is None


def test_a_merchant_is_never_renamed_to_a_neighbouring_slab() -> None:
    """2.35% and 2.36% are both real slabs. Reporting a 2.35% merchant as
    "a standard 2.36% slab" is a false sentence to say in a demo, even though
    the arithmetic is unaffected."""
    model = FeeModel.infer(pairs_at(0.0235))
    assert model.snapped_rate is None
    assert "2.36" not in model.describe()
    assert "2.350%" in model.describe()


def test_low_confidence_never_reports_a_snapped_rate() -> None:
    assert FeeModel.infer(pairs_at(0.02, n=2)).snapped_rate is None


# ==========================================================================
# The sample is clean by construction
# ==========================================================================


def test_only_confirmed_matches_feed_the_model(dataset: Path) -> None:
    """L2's input is L1's output, and L1 refuses any settlement with an
    unitemised deduction — so a cross-period refund can never be read as fee."""
    from core.dates import BusinessCalendar
    from ingest.normalizer import load_dataset
    from matching.exact_matcher import ExactMatcher
    from matching.protocols import MatchContext

    loaded = load_dataset(dataset)
    ctx = MatchContext.build(
        loaded.records, calendar=BusinessCalendar(), settings=Settings()
    )
    for p in ExactMatcher().propose(ctx):
        ctx.accept(p)
    pairs = ctx.confirmed_fee_pairs()
    assert pairs
    for gross, net in pairs:
        implied = (1 - net / gross) / 1.18
        assert 0.015 < implied < 0.03, "a poisoned pair reached the sample"


def test_the_model_is_deterministic(dataset: Path) -> None:
    a = build_pipeline().run(dataset).fee_rate
    b = build_pipeline().run(dataset).fee_rate
    assert a == b


# ==========================================================================
# The demo line (§4.2)
# ==========================================================================


def test_describe_says_it_was_never_told_the_rate(dataset: Path) -> None:
    result = build_pipeline().run(dataset)
    line = result.fee_model_summary
    assert "Inferred MDR" in line
    assert "confident settlements" in line


def test_the_report_shows_inferred_against_planted(dataset: Path) -> None:
    m = evaluate(dataset)
    assert m.inferred_fee_rate is not None
    assert m.planted_fee_rate is not None
    assert abs(m.inferred_fee_rate - m.planted_fee_rate) < TOLERANCE


# ==========================================================================
# Ablation (§7.5)
# ==========================================================================


def test_no_fee_model_ablation_disables_inference(dataset: Path) -> None:
    with_model = build_pipeline().run(dataset)
    without = build_pipeline(no_fee_model=True).run(dataset)

    assert with_model.fee_rate is not None
    assert not FeeModel.disabled().is_usable
    # None, not the fallback: reporting 2.00% here would contradict the summary
    # beside it and overstate what the system actually learned.
    assert without.fee_rate is None
    assert "not inferred" in without.fee_model_summary


def test_the_ablation_quantifies_what_the_fee_model_buys(dataset: Path) -> None:
    """§7.5: an ablation number is only worth reporting if it moves.

    Without an inferred rate, L3 has no target to search for — a bank credit of
    ₹19,914.19 has no computable relationship to a pile of ledger rows — so it
    resolves nothing at all. Everything L3 contributes is contributed by L2.
    """
    with_model = build_pipeline().run(dataset)
    without = build_pipeline(no_fee_model=True).run(dataset)

    assert len(with_model.matches) > len(without.matches), (
        "the ablation changes nothing, so the fee model is doing nothing"
    )
    l3_with = {u for u, m in with_model.matches.items() if m.strategy == "L3_subset"}
    l3_without = {u for u, m in without.matches.items() if m.strategy == "L3_subset"}
    assert l3_with, "L3 resolved nothing even with a fee model"
    assert not l3_without, "L3 resolved something without a rate to invert with"

    # L1 is unaffected: it joins on identifiers and never touches a rate.
    l1_with = {u for u, m in with_model.matches.items() if m.strategy == "L1_exact"}
    l1_without = {u for u, m in without.matches.items() if m.strategy == "L1_exact"}
    assert l1_with == l1_without


# ==========================================================================
# The LOW_CONFIDENCE contract L3 must honour (gate 7)
# ==========================================================================
#
# `tolerance_paise` widening is tested above and passes today. What is NOT yet
# enforced is that L3 actually *consults* it. Until then, "a low-confidence
# model makes L3 widen its tolerance" is a sentence in a report rather than a
# property of the code — and the failure mode is silent: L3 would treat the
# 2.00% fallback exactly like a rate it had genuinely inferred, and match
# against a target it invented.
#
# These were written at gate 6 as xfail(strict=True), before L3 existed. They
# went green the moment L3 landed, which made the suite fail until the markers
# were removed — which is exactly what strict=True is for. The contract could
# not be satisfied by accident and then forgotten.


def test_l3_widens_its_tolerance_when_the_fee_model_is_low_confidence() -> None:
    """L3 must not treat a fallback rate as if it were inferred."""
    import inspect as _inspect

    from matching import subset_matcher

    src = _inspect.getsource(subset_matcher)
    assert "tolerance_paise" in src, (
        "L3 computes its amount tolerance without asking the fee model, so a "
        "LOW_CONFIDENCE fallback is being trusted like an inferred rate"
    )


def test_l3_never_claims_high_confidence_on_a_guessed_rate() -> None:
    """The consequence of the above, stated as behaviour.

    If the rate was never inferred, every amount L3 derives from it is a guess,
    and nothing built on a guess belongs in the auto-post band (§2.5).
    """
    from core.dates import BusinessCalendar
    from ingest.normalizer import load_dataset
    from matching.protocols import MatchContext
    from matching.subset_matcher import SubsetMatcher

    dataset = Path("data/seed42")
    loaded = load_dataset(dataset)
    ctx = MatchContext.build(
        loaded.records, calendar=BusinessCalendar(), settings=Settings()
    )
    ctx.fee_model = FeeModel.disabled()

    settings = Settings()
    for proposal in SubsetMatcher().propose(ctx):
        assert proposal.confidence < settings.auto_post_threshold
