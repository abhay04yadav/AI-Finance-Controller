"""Fee-model fixtures that a broken L2 cannot pass. Guide §4.2, gate 6.

**Why this file exists.**

The default dataset plants an MDR of exactly 2.00%. §4.2 step 5 says L2 falls
back to `0.02` when it has fewer than five samples, and §4.2 step 4 says to snap
a near rate to a plausible one. Put together, a fee model that never ran at all
would return 0.02, be compared against a planted 0.02, and pass a "within 0.1%
of truth" check with a green tick.

So gate 6 must be judged on rates that are **not** round and **not** the
fallback. These tests build those datasets and prove the recovery is possible
from the files alone — independent of whatever L2 ends up doing — so that when
L2 arrives it is being measured, not flattered.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

from core.models import Source
from generator.generate import INTL_FEE_RATE, PLANTED_FEE_RATE, generate
from ingest.normalizer import load_dataset

#: Deliberately not 2.00%, and not near it. 1.75% and 2.35% are both real
#: negotiated MDR slabs, and neither equals L2's fallback.
NON_ROUND_RATES = (0.0175, 0.0235)

GST = 0.18
TOLERANCE = 0.001  # the gate 6 bar: within 0.1%


def recoverable_rates(dataset: Path) -> list[float]:
    """Invert the fee model from the files, the way L2 will have to (§4.2).

    Settlements with an unitemised deduction are skipped: there the shortfall is
    a cross-period refund, and counting it as fee would corrupt the sample.
    """
    loaded = load_dataset(dataset)
    return [
        rec.settlement().implied_fee_rate(GST)
        for rec in loaded.by_source(Source.SETTLEMENT)
        if rec.settlement().unitemised_paise == 0
        and rec.settlement().gross.paise > 0
    ]


# ==========================================================================
# The trap itself, stated as a test
# ==========================================================================


def test_the_default_dataset_rate_is_not_l2s_fallback() -> None:
    """The demo dataset must not sit on the fallback.

    Two things go wrong when it does. A fee model that never ran would return
    0.02, be compared against a planted 0.02, and pass. And the demo prints
    "Inferred MDR: 2.0000%", which is indistinguishable from a hardcoded
    constant to anyone watching — a round number is not evidence.
    """
    l2_fallback = 0.02  # §4.2 step 5
    assert l2_fallback != PLANTED_FEE_RATE
    assert abs(PLANTED_FEE_RATE - l2_fallback) > TOLERANCE, (
        f"the demo dataset plants {PLANTED_FEE_RATE:.4%}, too close to the "
        f"{l2_fallback:.2%} fallback to prove inference happened"
    )


def test_the_demo_rate_is_not_a_round_number() -> None:
    """"Inferred MDR: 2.0000%" persuades nobody. The number has to be one that
    could only have come from the data."""
    basis_points = PLANTED_FEE_RATE * 10_000
    assert basis_points % 5 != 0, (
        f"{PLANTED_FEE_RATE:.4%} is a round slab; pick a rate nobody would hardcode"
    )


def test_non_round_rates_are_far_enough_from_the_fallback_to_be_decisive() -> None:
    for rate in NON_ROUND_RATES:
        assert abs(rate - 0.02) > TOLERANCE * 2, (
            f"{rate} is too close to the fallback to prove anything"
        )


# ==========================================================================
# The datasets support recovery, so gate 6 measures L2 rather than luck
# ==========================================================================


@pytest.mark.parametrize("rate", NON_ROUND_RATES)
def test_a_non_round_rate_is_recoverable_from_the_files(
    tmp_path: Path, rate: float
) -> None:
    out = tmp_path / f"r{rate}"
    generate(42, 400, out, fee_rate=rate)

    rates = recoverable_rates(out)
    assert len(rates) >= 5, "too few clean samples for L2 to infer anything"

    recovered = statistics.median(rates)
    assert abs(recovered - rate) < TOLERANCE, (
        f"planted {rate:.4%}, recoverable {recovered:.4%} — the dataset itself "
        "does not support inference, so gate 6 would be measuring nothing"
    )


@pytest.mark.parametrize("rate", NON_ROUND_RATES)
def test_the_planted_rate_is_recorded_in_the_answer_key(
    tmp_path: Path, rate: float
) -> None:
    out = tmp_path / f"t{rate}"
    generate(42, 200, out, fee_rate=rate)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    assert truth["fee_rate"] == rate
    assert truth["fee_slabs"] == [rate]


def test_the_rate_never_reaches_the_agent(tmp_path: Path) -> None:
    """§2.3: the MDR is inferred, never configured. It lives in the answer key
    only, which the agent cannot read."""
    out = tmp_path / "hidden"
    generate(42, 200, out, fee_rate=0.0175)
    for name in ("ledger.csv", "settlement.csv", "bank.csv"):
        text = (out / name).read_text(encoding="utf-8")
        assert "0.0175" not in text
        assert "1.75" not in text


# ==========================================================================
# Multi-slab: the median must hold where a mean would not (§4.2 step 3)
# ==========================================================================


def test_international_rows_do_not_drag_the_median(tmp_path: Path) -> None:
    """A handful of 3.5% international settlements must not move the inferred
    domestic rate. §4.2 step 2 says median, not mean, for exactly this reason —
    a dragged rate mis-prices every credit downstream."""
    out = tmp_path / "slab"
    domestic = 0.0175
    generate(42, 500, out, fee_rate=domestic, intl_ratio=0.10, intl_rate=INTL_FEE_RATE)

    rates = recoverable_rates(out)
    median = statistics.median(rates)
    mean = statistics.fmean(rates)

    assert abs(median - domestic) < TOLERANCE, (
        f"median {median:.4%} drifted from the planted {domestic:.4%}"
    )
    assert abs(mean - domestic) > TOLERANCE, (
        "the outliers are too few or too mild to distinguish a median from a "
        "mean — this dataset would not catch the bug it exists to catch"
    )


def test_the_slab_dataset_actually_contains_both_slabs(tmp_path: Path) -> None:
    out = tmp_path / "slabs"
    generate(42, 400, out, fee_rate=0.0235, intl_ratio=0.10)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    assert truth["fee_slabs"] == [0.0235, INTL_FEE_RATE]
    planted = {e["type"] for e in truth["exceptions"]}
    assert "FX_OR_SLAB_VARIANCE" in planted


def test_slab_variance_is_opt_in(tmp_path: Path) -> None:
    """The standard dataset stays single-slab, so gate 5's L1 expectations do
    not move underneath it."""
    out = tmp_path / "plain"
    generate(42, 300, out)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    assert truth["fee_slabs"] == [PLANTED_FEE_RATE]
    assert "FX_OR_SLAB_VARIANCE" not in {e["type"] for e in truth["exceptions"]}


def test_outlier_settlements_really_carry_the_other_rate(tmp_path: Path) -> None:
    out = tmp_path / "check"
    generate(42, 400, out, fee_rate=0.0175, intl_ratio=0.10)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    flagged = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "FX_OR_SLAB_VARIANCE"
    }
    assert flagged

    loaded = load_dataset(out)
    for rec in loaded.by_source(Source.SETTLEMENT):
        d = rec.settlement()
        if d.utr in flagged and d.unitemised_paise == 0:
            assert abs(d.implied_fee_rate(GST) - INTL_FEE_RATE) < TOLERANCE


# ==========================================================================
# Determinism carries over to the new flags
# ==========================================================================


def test_fee_rate_datasets_are_still_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(7, 200, a, fee_rate=0.0175, intl_ratio=0.1)
    generate(7, 200, b, fee_rate=0.0175, intl_ratio=0.1)
    for name in ("ledger.csv", "settlement.csv", "bank.csv", "truth.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_a_different_rate_produces_different_files(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(7, 200, a, fee_rate=0.0175)
    generate(7, 200, b, fee_rate=0.0235)
    assert (a / "bank.csv").read_bytes() != (b / "bank.csv").read_bytes()
