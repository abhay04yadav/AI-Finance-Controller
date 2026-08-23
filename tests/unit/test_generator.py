"""The generator — exam paper and answer key. Guide §6.

These tests protect the property everything downstream rests on: if the dataset
is wrong, every metric measured against it is meaningless, and the failure looks
exactly like a matcher bug three gates later.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from core.config import Settings
from core.money import Money
from generator.generate import build_world, generate
from generator.injectors import build_registry, count_for
from generator.validate import GeneratorInvariantError, validate
from generator.world import OrderStatus
from generator.writers import assert_no_order_ids, narration_for, rupees

SEED = 42


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("seed42")
    generate(SEED, 500, out)
    return out


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def truth_of(d: Path) -> dict:
    return json.loads((d / "truth.json").read_text(encoding="utf-8"))


# ==========================================================================
# THE check. If this fails, every number this project reports is worthless.
# ==========================================================================


def test_bank_narration_contains_no_order_ids(dataset: Path) -> None:
    """§6.2 step 4, and the one thing the Review Guide says to check by eye.

    Leaking order IDs into the narration makes the problem trivially solvable
    and turns a 100% score into a meaningless one.
    """
    blob = (dataset / "bank.csv").read_text(encoding="utf-8").upper()
    assert "ORD-" not in blob
    assert "RFND-" not in blob


def test_a_leak_would_be_caught_at_write_time() -> None:
    """The guard is real, not decorative — prove it rejects a leak."""
    with pytest.raises(AssertionError, match="leaked"):
        assert_no_order_ids([["2026-08-04", "100.00", "CR", "NEFT ORD-1001 CR", "UTR-1"]])


def test_narration_looks_like_a_real_bank_statement(dataset: Path) -> None:
    for row in read(dataset / "bank.csv"):
        n = row["narration"]
        assert "RAZORPAY" in n.upper() or "RZP" in n.upper()
        assert "ORD" not in n.upper()


# ==========================================================================
# Determinism — the reproducibility claim is a checksum claim (§2.7 rule 2)
# ==========================================================================


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for name in sorted(p.name for p in d.iterdir()):
        h.update((d / name).read_bytes())
    return h.hexdigest()


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(SEED, 300, a)
    generate(SEED, 300, b)
    assert _digest(a) == _digest(b)


def test_different_seed_produces_different_data(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(SEED, 300, a)
    generate(7, 300, b)
    assert _digest(a) != _digest(b)


def test_files_use_lf_endings(dataset: Path) -> None:
    """CRLF would make a Windows run differ from a Linux one, silently breaking
    the byte-identical guarantee a judge is invited to reproduce."""
    for name in ("ledger.csv", "settlement.csv", "bank.csv", "truth.json"):
        assert b"\r\n" not in (dataset / name).read_bytes()


def test_generated_at_is_not_the_wall_clock(dataset: Path) -> None:
    """A timestamp would make two runs of one seed differ (§9.2)."""
    t = truth_of(dataset)
    assert t["generated_at"] == t["period"]["end"]


# ==========================================================================
# All eight failure modes, at every scale (Review Guide gate 2)
# ==========================================================================


@pytest.mark.parametrize("scale", [50, 250, 500])
def test_all_eight_failure_modes_appear(tmp_path: Path, scale: int) -> None:
    out = tmp_path / f"s{scale}"
    generate(SEED, scale, out)
    planted = {e["type"] for e in truth_of(out)["exceptions"]}
    expected = {str(i.reason_code) for i in build_registry()}
    assert planted == expected, f"missing at scale {scale}: {sorted(expected - planted)}"


def test_registry_has_exactly_eight_injectors() -> None:
    registry = build_registry()
    assert len(registry) == 8
    assert len({i.reason_code for i in registry}) == 8


def test_exception_counts_scale_with_scale(tmp_path: Path) -> None:
    """Ratios, not fixed numbers — the gate 2 stop condition."""
    counts = {}
    for scale in (250, 2000):
        out = tmp_path / f"s{scale}"
        generate(SEED, scale, out)
        counts[scale] = len(truth_of(out)["exceptions"])
    assert counts[2000] > counts[250] * 3


def test_count_for_floors_at_one() -> None:
    """0.4% of 50 rounds to zero; the floor keeps every mode exercised at the
    50-record bar the brief sets."""
    assert count_for(0.004, 50) == 1
    assert count_for(0.004, 5000) == 20


def test_every_injector_documents_the_behaviour_it_models() -> None:
    """Gate 2 asks, per injector: which real Razorpay or RBI behaviour is this?
    An injector that cannot answer is an invented failure mode."""
    for inj in build_registry():
        assert inj.models and len(inj.models) > 30
        assert "§" in inj.models
        assert inj.unit in ("order", "batch")


# ==========================================================================
# Partial settlement defers WHOLE transactions (the gate 2 stop condition)
# ==========================================================================


def test_partial_settlement_never_splits_an_amount(dataset: Path) -> None:
    """§1.3: the gateway settles a subset of whole transactions and defers the
    rest whole. Splitting an amount would make N:1 matching impossible."""
    ledger = {r["order_id"]: r for r in read(dataset / "ledger.csv")}
    deferred = [
        e["ref"]
        for e in truth_of(dataset)["exceptions"]
        if e["type"] == "AWAITING_SETTLEMENT"
    ]
    assert deferred
    settled = {r["order_id"] for r in read(dataset / "settlement.csv")}
    for ref in deferred:
        # Present in the books, absent from every settlement — held whole.
        assert ref in ledger
        assert ref not in settled
        assert ledger[ref]["status"] == OrderStatus.CAPTURED


def test_settlement_amounts_are_whole_order_amounts(dataset: Path) -> None:
    """Every settled gross is exactly the sum of its members' ledger amounts."""
    ledger = {r["order_id"]: Money.from_rupee_string(r["amount"]).paise
              for r in read(dataset / "ledger.csv")}
    rows = read(dataset / "settlement.csv")
    by_settlement: dict[str, list[str]] = {}
    grosses: dict[str, int] = {}
    for r in rows:
        by_settlement.setdefault(r["settlement_id"], []).append(r["order_id"])
        grosses[r["settlement_id"]] = Money.from_rupee_string(r["gross"]).paise
    for sid, members in by_settlement.items():
        known = [ledger[m] for m in members if m in ledger]
        if len(known) == len(members):  # skip MISSING_IN_LEDGER batches
            assert sum(known) == grosses[sid]


# ==========================================================================
# The money arithmetic ties (§1.4 worked example)
# ==========================================================================


def test_fee_and_gst_follow_the_documented_model(dataset: Path) -> None:
    t = truth_of(dataset)
    drifted = {e["ref"] for e in t["exceptions"] if e["type"] == "ROUNDING_DRIFT"}
    for r in read(dataset / "settlement.csv"):
        if r["utr"] in drifted:
            continue
        gross = Money.from_rupee_string(r["gross"]).paise
        fee = Money.from_rupee_string(r["fee"]).paise
        gst = Money.from_rupee_string(r["gst"]).paise
        assert fee == int(gross * t["fee_rate"])
        assert gst == int(fee * t["gst_rate"])


def test_bank_credit_equals_the_settlement_net(dataset: Path) -> None:
    nets = {r["utr"]: Money.from_rupee_string(r["net"]).paise
            for r in read(dataset / "settlement.csv")}
    for r in read(dataset / "bank.csv"):
        assert Money.from_rupee_string(r["amount"]).paise == nets[r["utr"]]


def test_rounding_drift_stays_under_fifty_paise(dataset: Path) -> None:
    """§4.2: 1–50 paise. Larger would be an AMOUNT_MISMATCH, not drift."""
    t = truth_of(dataset)
    drifted = {e["ref"] for e in t["exceptions"] if e["type"] == "ROUNDING_DRIFT"}
    seen = 0
    for r in read(dataset / "settlement.csv"):
        if r["utr"] not in drifted:
            continue
        gross = Money.from_rupee_string(r["gross"]).paise
        fee = Money.from_rupee_string(r["fee"]).paise
        assert 0 < abs(fee - int(gross * t["fee_rate"])) <= 50
        seen += 1
    assert seen


def test_no_float_in_the_written_amounts(dataset: Path) -> None:
    """Every amount cell must round-trip through Money exactly."""
    for name, col in (("ledger.csv", "amount"), ("bank.csv", "amount")):
        for r in read(dataset / name):
            assert Money.from_rupee_string(r[col]).paise == round(float(r[col]) * 100)


def test_rupees_formats_without_float() -> None:
    assert rupees(781120) == "7811.20"
    assert rupees(-24000) == "-240.00"
    assert rupees(1) == "0.01"


# ==========================================================================
# Each failure mode is planted the way its documented behaviour says
# ==========================================================================


def test_duplicate_utr_appears_exactly_twice(dataset: Path) -> None:
    utrs = [r["utr"] for r in read(dataset / "bank.csv")]
    planted = [e["ref"] for e in truth_of(dataset)["exceptions"]
               if e["type"] == "DUPLICATE_UTR"]
    assert planted
    for utr in planted:
        assert utrs.count(utr) == 2
    # and nothing else is duplicated by accident
    for utr in set(utrs):
        assert utrs.count(utr) == 1 or utr in planted


def test_auto_refunded_orders_never_settle(dataset: Path) -> None:
    """§1.3 phase 3: not captured within 3 days, so the sale never settles."""
    ledger = {r["order_id"]: r for r in read(dataset / "ledger.csv")}
    settled = {r["order_id"] for r in read(dataset / "settlement.csv")}
    refs = [e["ref"] for e in truth_of(dataset)["exceptions"]
            if e["type"] == "AUTO_REFUNDED"]
    assert refs
    for ref in refs:
        assert ledger[ref]["status"] == OrderStatus.AUTHORIZED
        assert ref not in settled


def test_late_authorization_settles_despite_a_failed_ledger_status(dataset: Path) -> None:
    """The bank credit exists for an order the books marked failed (§1.3)."""
    ledger = {r["order_id"]: r for r in read(dataset / "ledger.csv")}
    settled = {r["order_id"] for r in read(dataset / "settlement.csv")}
    refs = [e["ref"] for e in truth_of(dataset)["exceptions"]
            if e["type"] == "LATE_AUTHORIZATION"]
    assert refs
    for ref in refs:
        assert ledger[ref]["status"] == OrderStatus.FAILED
        assert ref in settled, "the money did arrive — only the status is stale"


def test_cross_period_refund_predates_the_settlement_window(dataset: Path) -> None:
    """§4.3b: the asymmetry between the order window and the refund lookback is
    only meaningful if the refund is genuinely old."""
    ledger = {r["order_id"]: r for r in read(dataset / "ledger.csv")}
    settle_dates = {r["utr"]: r["settle_date"] for r in read(dataset / "settlement.csv")}
    t = truth_of(dataset)
    refs = [e["ref"] for e in t["exceptions"] if e["type"] == "CROSS_PERIOD_REFUND"]
    assert refs
    for ref in refs:
        assert ref in ledger
        assert ledger[ref]["status"] == OrderStatus.REFUND
        assert ledger[ref]["amount"].startswith("-"), "refunds carry a negative amount"
        utr = next(u for u, members in t["mappings"].items() if ref in members)
        assert ledger[ref]["capture_date"] < settle_dates[utr]


def test_cross_period_refund_is_not_itemised_in_the_settlement(dataset: Path) -> None:
    """What makes the batch total 'unexplainably short' (§1.5)."""
    itemised = {r["order_id"] for r in read(dataset / "settlement.csv")}
    refs = [e["ref"] for e in truth_of(dataset)["exceptions"]
            if e["type"] == "CROSS_PERIOD_REFUND"]
    for ref in refs:
        assert ref not in itemised


def test_missing_in_ledger_has_bank_money_but_no_books(dataset: Path) -> None:
    ledger = {r["order_id"] for r in read(dataset / "ledger.csv")}
    utrs = {r["utr"] for r in read(dataset / "bank.csv")}
    t = truth_of(dataset)
    refs = [e["ref"] for e in t["exceptions"] if e["type"] == "MISSING_IN_LEDGER"]
    assert refs
    for utr in refs:
        assert utr in utrs, "the money really did arrive"
        assert utr not in t["mappings"], "there is nothing in the books to map to"
        for r in read(dataset / "settlement.csv"):
            if r["utr"] == utr:
                assert r["order_id"] not in ledger


def test_holiday_shift_lands_later_than_naive_t_plus_two(dataset: Path) -> None:
    """Credit falls outside a naive date window (§1.5)."""
    from datetime import date, timedelta

    settle = {r["utr"]: date.fromisoformat(r["settle_date"])
              for r in read(dataset / "settlement.csv")}
    captures: dict[str, list[date]] = {}
    ledger = {r["order_id"]: date.fromisoformat(r["capture_date"])
              for r in read(dataset / "ledger.csv")}
    t = truth_of(dataset)
    for utr, members in t["mappings"].items():
        captures[utr] = [ledger[m] for m in members if m in ledger]
    refs = [e["ref"] for e in t["exceptions"] if e["type"] == "HOLIDAY_SHIFT"]
    assert refs
    for utr in refs:
        assert settle[utr] > min(captures[utr]) + timedelta(days=2)


# ==========================================================================
# The answer key resolves, and self-validation actually bites
# ==========================================================================


def test_every_mapping_resolves_to_real_ledger_rows(dataset: Path) -> None:
    ledger = {r["order_id"] for r in read(dataset / "ledger.csv")}
    utrs = {r["utr"] for r in read(dataset / "bank.csv")}
    for utr, members in truth_of(dataset)["mappings"].items():
        assert utr in utrs
        assert members
        assert set(members) <= ledger


def test_truth_json_has_the_documented_shape(dataset: Path) -> None:
    t = truth_of(dataset)
    assert {"seed", "scale", "fee_rate", "gst_rate", "mappings", "exceptions",
            "generator_version", "generated_at"} <= set(t)
    assert t["seed"] == SEED
    assert all(isinstance(v, list) for v in t["mappings"].values())
    assert all({"ref", "type"} == set(e) for e in t["exceptions"])


def test_validation_catches_a_corrupted_answer_key(tmp_path: Path) -> None:
    """Self-validation must bite, not just run (§6.3)."""
    out = tmp_path / "corrupt"
    generate(SEED, 200, out)
    t = truth_of(out)
    utr = next(iter(t["mappings"]))
    t["mappings"][utr] = ["ORD-DOES-NOT-EXIST"]
    (out / "truth.json").write_text(json.dumps(t), encoding="utf-8")
    with pytest.raises(GeneratorInvariantError, match="absent from ledger"):
        validate(out)


def test_validation_catches_a_missing_failure_mode(tmp_path: Path) -> None:
    """The assertion whose absence let a 4-of-8 dataset ship as 'passed'."""
    out = tmp_path / "thin"
    generate(SEED, 200, out)
    t = truth_of(out)
    # HOLIDAY_SHIFT deliberately: removing DUPLICATE_UTR trips the earlier
    # duplicate check instead, so it would not exercise this assertion.
    t["exceptions"] = [e for e in t["exceptions"] if e["type"] != "HOLIDAY_SHIFT"]
    (out / "truth.json").write_text(json.dumps(t), encoding="utf-8")
    with pytest.raises(GeneratorInvariantError, match="failure modes missing"):
        validate(out)


def test_validation_catches_a_broken_bank_amount(tmp_path: Path) -> None:
    out = tmp_path / "badmoney"
    generate(SEED, 200, out)
    rows = read(out / "bank.csv")
    rows[0]["amount"] = "999999.99"
    with (out / "bank.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(GeneratorInvariantError, match="disagrees with the settlement"):
        validate(out)


# ==========================================================================
# Shape of the world
# ==========================================================================


def test_batches_are_n_to_one_not_one_to_one(dataset: Path) -> None:
    """If most credits mapped to a single order there would be no problem to
    solve — the whole difficulty is N:1 (§1.4 reason 1)."""
    sizes = [len(m) for m in truth_of(dataset)["mappings"].values()]
    assert sum(sizes) / len(sizes) > 3


def test_the_agent_never_sees_the_fee_rate(dataset: Path) -> None:
    """§2.3: the MDR is inferred, never configured. It appears only in the
    answer key, which the agent cannot read."""
    for name in ("ledger.csv", "settlement.csv", "bank.csv"):
        text = (dataset / name).read_text(encoding="utf-8")
        assert "0.02" not in text
        assert "fee_rate" not in text


def test_world_batches_derive_totals_rather_than_storing_them() -> None:
    """A stale stored total is the generator bug that looks like a matcher bug."""
    world = build_world(SEED, 200, Settings())
    batch = next(b for b in world.batches if len(b.order_ids) > 2)
    before = batch.gross(world)
    removed = batch.order_ids[0]
    batch.order_ids.remove(removed)
    assert batch.gross(world) == before - world.orders[removed].amount_paise


def test_narration_is_stable_for_a_settlement() -> None:
    world = build_world(SEED, 100, Settings())
    b = world.batches[0]
    assert narration_for(b) == narration_for(b)
    assert b.settlement_id.split("-")[-1] in narration_for(b)
