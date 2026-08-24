"""L0 — ingest and normalize. Guide §4.0.

L0 is where a reconciliation system quietly loses money: a float amount, a
guessed date, a silently repaired row. Every test here is about refusing to do
that.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from core.models import Direction, Record, Source
from core.money import Money
from core.reason_codes import ReasonCode
from generator.generate import generate
from ingest.bank_adapter import BankAdapter
from ingest.ledger_adapter import LedgerAdapter
from ingest.normalizer import (
    IngestError,
    extract_refs,
    find_illegal_duplicates,
    load_dataset,
    parse_amount,
    parse_business_date,
)
from ingest.settlement_adapter import SettlementAdapter


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("l0")
    generate(42, 400, out)
    return out


@pytest.fixture(scope="module")
def loaded(dataset: Path):
    return load_dataset(dataset)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, header: list[str], data: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(data)


# ==========================================================================
# The §4.0 verification block, verbatim
# ==========================================================================


def test_l0_amount_is_never_float(loaded) -> None:
    for r in loaded.records:
        assert isinstance(r.amount.paise, int)
        assert not isinstance(r.amount.paise, bool)


def test_l0_totals_survive_normalization(dataset: Path, loaded) -> None:
    """Sum of parsed rupee strings == sum of paise, to the paise."""
    expected = sum(
        abs(Money.from_rupee_string(r["amount"]).paise)
        for r in rows(dataset / "ledger.csv")
    )
    actual = sum(r.amount.paise for r in loaded.by_source(Source.LEDGER))
    assert actual == expected


def test_l0_bank_total_ties_to_the_file(dataset: Path, loaded) -> None:
    expected = sum(
        Money.from_rupee_string(r["amount"]).paise for r in rows(dataset / "bank.csv")
    )
    assert loaded.total_paise(Source.BANK) == expected


def test_l0_settlement_total_ties_to_the_distinct_settlements(
    dataset: Path, loaded
) -> None:
    """The file is long-format, so the total is over settlements, not rows."""
    nets = {
        r["settlement_id"]: Money.from_rupee_string(r["net"]).paise
        for r in rows(dataset / "settlement.csv")
    }
    assert loaded.total_paise(Source.SETTLEMENT) == sum(nets.values())


def test_l0_is_idempotent(dataset: Path) -> None:
    assert load_dataset(dataset).records == load_dataset(dataset).records


def test_l0_ordering_is_stable_not_set_ordered(dataset: Path) -> None:
    """§9.1: unsorted iteration makes two runs differ and costs an hour to find."""
    ids = [r.external_id for r in load_dataset(dataset).records]
    assert ids == [r.external_id for r in load_dataset(dataset).records]


@pytest.mark.parametrize("bad", ["03/04/2026", "3-4-2026", "13/04/2026", "03.04.26"])
def test_l0_rejects_ambiguous_dates(bad: str) -> None:
    """d/m or m/d? Refuse to guess (§4.0 step 3).

    `13/04/2026` is rejected too. It happens to be unambiguous by value, but
    accepting it while rejecting `03/04/2026` would make the parser's behaviour
    depend on the data — worse than refusing the whole format class.
    """
    with pytest.raises(IngestError, match="ambiguous"):
        parse_business_date(bad)


# ==========================================================================
# Dates
# ==========================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-08-04", date(2026, 8, 4)),
        ("2026/08/04", date(2026, 8, 4)),
        ("04-Aug-2026", date(2026, 8, 4)),
        ("4 August 2026", date(2026, 8, 4)),
        ("Aug 4, 2026", date(2026, 8, 4)),
        ("  2026-08-04  ", date(2026, 8, 4)),
    ],
)
def test_self_describing_dates_are_accepted(text: str, expected: date) -> None:
    assert parse_business_date(text) == expected


def test_utc_timestamps_convert_to_the_indian_business_date() -> None:
    """§9.2: a UTC instant after 18:30 IST belongs to the NEXT Indian business
    day. Taking `.date()` off the UTC value would shift a settlement window."""
    assert parse_business_date("2026-08-04T19:00:00+00:00") == date(2026, 8, 5)
    assert parse_business_date("2026-08-04T12:00:00+00:00") == date(2026, 8, 4)
    assert parse_business_date("2026-08-04T23:30:00Z") == date(2026, 8, 5)


def test_naive_datetimes_are_rejected() -> None:
    """No timezone means the business date is undefined (§2.7 rule 6)."""
    with pytest.raises(IngestError, match="naive"):
        parse_business_date("2026-08-04T19:00:00")


@pytest.mark.parametrize("bad", ["", "   ", "not a date", "2026-13-45", "20260804"])
def test_unparseable_dates_are_rejected(bad: str) -> None:
    with pytest.raises(IngestError):
        parse_business_date(bad)


# ==========================================================================
# Amounts — straight to paise, never via float
# ==========================================================================


def test_amounts_parse_straight_to_paise() -> None:
    assert parse_amount("7811.20") == Money(781120)
    assert parse_amount("-240.00") == Money(-24000)


def test_amount_parsing_never_routes_through_float() -> None:
    """The gate 4 stop condition. `float("0.1") * 100` is 10.000000000000002,
    and `int()` of that truncates to 10 — right here, wrong one cent later."""
    import ast
    import inspect

    from core import money
    from ingest import normalizer

    for module in (money, normalizer):
        tree = ast.parse(inspect.getsource(module))
        calls = [
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "float" not in calls, f"float() in the money path: {module.__name__}"


def test_unparseable_amount_raises_ingest_error() -> None:
    with pytest.raises(IngestError, match="unparseable amount"):
        parse_amount("abc")


def test_sub_paise_precision_is_refused() -> None:
    with pytest.raises(IngestError):
        parse_amount("100.005")


# ==========================================================================
# Reference tokens (§3.4, §4.0 step 4)
# ==========================================================================


def test_refs_find_the_settlement_number_in_a_real_narration() -> None:
    """§4.0's generic pattern needs four digits, so it finds nothing in
    "SETL101". The "plus known prefixes" clause is what makes the narration
    usable as the signal L4 needs (§4.4 job A)."""
    assert "SETL101" in extract_refs("NEFT-RAZORPAY-SETL101-CR")
    assert "SETL102" in extract_refs("IMPS/RAZORPAY/SETL102")
    assert "SETL104" in extract_refs("NEFT RZPSETL104 COLLECTIONS CR")


def test_refs_find_long_form_identifiers() -> None:
    assert "UTR-77291234" in extract_refs("NEFT UTR-77291234 CR")


def test_refs_are_a_set_across_several_columns() -> None:
    refs = extract_refs("SETL-88", "UTR-77291234", "ORD-1001")
    assert {"SETL-88", "UTR-77291234", "ORD-1001"} <= refs


def test_refs_ignore_plain_noise() -> None:
    assert extract_refs("NEFT TRANSFER CREDIT") == frozenset()


def test_bank_records_carry_refs_from_the_narration(loaded) -> None:
    bank = loaded.by_source(Source.BANK)
    assert all(r.refs for r in bank)


# ==========================================================================
# A malformed row becomes INGEST_ERROR — never skipped, never repaired
# ==========================================================================


def test_a_bad_row_becomes_an_ingest_error_not_a_silent_skip(tmp_path: Path) -> None:
    """§4.0's trap. Silent repair is how reconciliation systems lose money."""
    path = tmp_path / "ledger.csv"
    write_csv(
        path,
        ["order_id", "amount", "capture_date", "status"],
        [
            ["ORD-1", "100.00", "2026-08-04", "captured"],
            ["ORD-2", "not-a-number", "2026-08-04", "captured"],
            ["ORD-3", "300.00", "03/04/2026", "captured"],
            ["ORD-4", "400.00", "2026-08-05", "captured"],
        ],
    )
    result = LedgerAdapter().load(path)

    assert len(result.records) == 2, "good rows are still read"
    assert len(result.failures) == 2, "bad rows are reported, not dropped"
    assert {f.line_no for f in result.failures} == {3, 4}
    assert all(f.reason_code is ReasonCode.INGEST_ERROR for f in result.failures)


def test_an_ingest_failure_preserves_the_original_row(tmp_path: Path) -> None:
    """A controller needs to see what actually arrived, not a cleaned version."""
    path = tmp_path / "ledger.csv"
    write_csv(
        path,
        ["order_id", "amount", "capture_date", "status"],
        [["ORD-9", "£££", "2026-08-04", "captured"]],
    )
    failure = LedgerAdapter().load(path).failures[0]
    assert failure.raw["amount"] == "£££"
    assert failure.raw["order_id"] == "ORD-9"


def test_one_bad_row_does_not_abort_the_run(tmp_path: Path) -> None:
    path = tmp_path / "bank.csv"
    write_csv(
        path,
        ["value_date", "amount", "type", "narration", "utr"],
        [
            ["2026-08-04", "100.00", "CR", "NEFT SETL101", "UTR-1"],
            ["2026-08-04", "200.00", "XX", "NEFT SETL102", "UTR-2"],
            ["2026-08-05", "300.00", "CR", "NEFT SETL103", "UTR-3"],
        ],
    )
    result = BankAdapter().load(path)
    assert len(result.records) == 2
    assert "unknown transaction type" in result.failures[0].reason


def test_a_wrong_header_fails_once_not_per_row(tmp_path: Path) -> None:
    """A file with the wrong columns is a different file, not N broken rows."""
    path = tmp_path / "ledger.csv"
    write_csv(path, ["a", "b", "c"], [["1", "2", "3"], ["4", "5", "6"]])
    result = LedgerAdapter().load(path)
    assert result.records == ()
    assert len(result.failures) == 1
    assert "missing column" in result.failures[0].reason


def test_a_missing_file_is_reported_not_crashed(tmp_path: Path) -> None:
    result = load_dataset(tmp_path)
    assert result.records == ()
    assert len(result.failures) == 3
    assert all("not found" in f.reason for f in result.failures)


# ==========================================================================
# Duplicates (§4.0 step 5)
# ==========================================================================


def test_duplicate_bank_utrs_are_preserved_not_collapsed(dataset: Path, loaded) -> None:
    """A repeated UTR is a real signal (DUPLICATE_UTR), not an ingest fault.
    Collapsing it here would let L1 double-post revenue."""
    import collections
    import json

    counts = collections.Counter(
        r.external_id for r in loaded.by_source(Source.BANK)
    )
    duplicated = {utr for utr, n in counts.items() if n > 1}
    truth = json.loads((dataset / "truth.json").read_text(encoding="utf-8"))
    planted = {e["ref"] for e in truth["exceptions"] if e["type"] == "DUPLICATE_UTR"}
    assert duplicated == planted


def test_duplicate_bank_utrs_raise_no_ingest_failure(loaded) -> None:
    assert not any(f.source is Source.BANK for f in loaded.failures)


def test_a_duplicate_ledger_id_is_an_ingest_failure() -> None:
    """Only bank rows may legitimately repeat."""
    def rec(external_id: str) -> Record:
        return Record(
            source=Source.LEDGER,
            external_id=external_id,
            amount=Money(100),
            value_date=date(2026, 8, 4),
            direction=Direction.INFLOW,
        )

    failures = find_illegal_duplicates((rec("ORD-1"), rec("ORD-1"), rec("ORD-2")))
    assert len(failures) == 1
    assert "duplicate" in failures[0].reason


# ==========================================================================
# The settlement adapter aggregates the long-format file
# ==========================================================================


def test_one_record_per_settlement_not_per_row(dataset: Path, loaded) -> None:
    distinct = {r["settlement_id"] for r in rows(dataset / "settlement.csv")}
    assert len(loaded.by_source(Source.SETTLEMENT)) == len(distinct)


def test_settlement_record_carries_its_members_and_totals(loaded) -> None:
    """The bridge document: order IDs on one side, the UTR on the other."""
    rec = loaded.by_source(Source.SETTLEMENT)[0]
    detail = rec.settlement()
    assert detail.order_ids
    assert detail.utr.startswith("UTR-")
    assert rec.amount == detail.net
    assert detail.gross.paise > detail.net.paise
    assert rec.refs >= {detail.utr, rec.external_id}


def test_settlement_money_is_typed_not_a_raw_dict(loaded) -> None:
    """L1 needs gross for its sum check and L2 needs (gross, net) pairs. Behind
    `dict[str, Any]` a typo would silently yield a wrong fee rate; as fields,
    mypy --strict covers the path."""
    from core.money import Money

    for rec in loaded.by_source(Source.SETTLEMENT):
        d = rec.settlement()
        assert isinstance(d.gross, Money)
        assert isinstance(d.fee, Money)
        assert isinstance(d.gst, Money)
        assert isinstance(d.net, Money)
        assert d.charges == d.fee + d.gst
    # money must not be reachable through raw at all any more
    assert not {k for k in loaded.by_source(Source.SETTLEMENT)[0].raw if "paise" in k}


def test_only_settlement_records_carry_detail(loaded) -> None:
    for rec in loaded.records:
        if rec.source is Source.SETTLEMENT:
            assert rec.detail is not None
        else:
            assert rec.detail is None
            with pytest.raises(TypeError, match="not a settlement record"):
                rec.settlement()


def test_implied_fee_rate_inverts_the_documented_model(dataset: Path, loaded) -> None:
    """§4.2: r = (1 - net/gross) / 1.18. Settlements with an unitemised
    deduction are excluded — there the shortfall is a refund, not fee.

    Compared against what the answer key actually planted, not a hardcoded
    2.00%: the demo rate is deliberately non-round so that recovering it means
    something (§4.2).
    """
    import json
    import statistics

    planted = json.loads((dataset / "truth.json").read_text(encoding="utf-8"))["fee_rate"]
    rates = [
        rec.settlement().implied_fee_rate(0.18)
        for rec in loaded.by_source(Source.SETTLEMENT)
        if rec.settlement().unitemised_paise == 0
    ]
    assert rates
    assert abs(statistics.median(rates) - planted) < 0.001


def test_unitemised_paise_reveals_a_cross_period_refund(loaded) -> None:
    """The shortfall L3 has to explain (§4.3b)."""
    shortfalls = [
        rec.settlement().unitemised_paise
        for rec in loaded.by_source(Source.SETTLEMENT)
    ]
    assert any(s > 0 for s in shortfalls), "no cross-period refund is visible"
    assert all(s >= 0 for s in shortfalls)


def test_settlement_refs_bridge_both_sides(loaded) -> None:
    ledger_ids = {r.external_id for r in loaded.by_source(Source.LEDGER)}
    bank_utrs = {r.external_id for r in loaded.by_source(Source.BANK)}
    rec = loaded.by_source(Source.SETTLEMENT)[0]
    assert rec.refs & bank_utrs, "no UTR to reach the bank statement"
    assert rec.refs & ledger_ids, "no order id to reach the ledger"


def test_a_self_contradicting_settlement_is_rejected(tmp_path: Path) -> None:
    """Two different nets for one settlement is corruption, not untidiness."""
    path = tmp_path / "settlement.csv"
    write_csv(
        path,
        list(
            ("settlement_id", "utr", "settle_date", "gross", "fee", "gst", "net", "order_id")
        ),
        [
            ["SETL-1", "UTR-1", "2026-08-04", "100.00", "2.00", "0.36", "97.64", "ORD-1"],
            ["SETL-1", "UTR-1", "2026-08-04", "100.00", "2.00", "0.36", "99.99", "ORD-2"],
        ],
    )
    result = SettlementAdapter().load(path)
    assert any("two different nets" in f.reason for f in result.failures)


# ==========================================================================
# Direction and the canonical model
# ==========================================================================


def test_refunds_become_outflows(loaded) -> None:
    refunds = [
        r for r in loaded.by_source(Source.LEDGER) if r.external_id.startswith("RFND-")
    ]
    assert refunds
    for r in refunds:
        assert r.direction is Direction.OUTFLOW
        assert r.signed_amount < 0
        assert r.amount.paise > 0, "the sign lives in direction, not the amount"


def test_sales_become_inflows(loaded) -> None:
    sales = [
        r for r in loaded.by_source(Source.LEDGER) if r.external_id.startswith("ORD-")
    ]
    assert all(r.direction is Direction.INFLOW for r in sales)


def test_debit_rows_become_outflows(tmp_path: Path) -> None:
    path = tmp_path / "bank.csv"
    write_csv(
        path,
        ["value_date", "amount", "type", "narration", "utr"],
        [["2026-08-04", "100.00", "DR", "REVERSAL", "UTR-9"]],
    )
    rec = BankAdapter().load(path).records[0]
    assert rec.direction is Direction.OUTFLOW
    assert rec.signed_amount == -10000


def test_every_record_keeps_its_raw_row(loaded) -> None:
    """Needed by L1 and L2, and by the audit trail (§9.3)."""
    assert all(r.raw for r in loaded.records)


def test_no_layer_downstream_needs_to_know_column_names(loaded) -> None:
    """The point of L0: three inconsistent files, one canonical schema."""
    for r in loaded.records:
        assert isinstance(r, Record)
        assert isinstance(r.amount, Money)
        assert isinstance(r.value_date, date)
        assert r.source in (Source.LEDGER, Source.SETTLEMENT, Source.BANK)


def test_a_clean_dataset_produces_no_failures(loaded) -> None:
    assert loaded.failures == ()
