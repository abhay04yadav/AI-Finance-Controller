"""Record, Source, Direction — the canonical data model. Guide §3.4."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from core.models import Direction, Record, Source
from core.money import Money

VALUE_DATE = date(2026, 8, 4)


def make(
    direction: Direction = Direction.INFLOW,
    paise: int = 800000,
    external_id: str = "ORD-101",
    source: Source = Source.LEDGER,
    **kw: object,
) -> Record:
    return Record(
        source=source,
        external_id=external_id,
        amount=Money(paise),
        value_date=VALUE_DATE,
        direction=direction,
        **kw,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# signed_amount — why refunds need no special case
# --------------------------------------------------------------------------


def test_inflow_is_positive() -> None:
    assert make(Direction.INFLOW).signed_amount == 800000


def test_outflow_is_negative() -> None:
    assert make(Direction.OUTFLOW).signed_amount == -800000


def test_refund_nets_out_of_a_batch_through_plain_addition() -> None:
    """§4.3a: `2000 + 3500 + 2500 - 1200 = 6800` falls out of one code path.

    The subset solver never learns what a refund is; it only sees integers.
    """
    pool = [
        make(paise=200000, external_id="ORD-1"),
        make(paise=350000, external_id="ORD-2"),
        make(paise=250000, external_id="ORD-3"),
        make(Direction.OUTFLOW, paise=120000, external_id="RFND-9"),
    ]
    assert sum(r.signed_amount for r in pool) == 680000


def test_is_inflow() -> None:
    assert make(Direction.INFLOW).is_inflow
    assert not make(Direction.OUTFLOW).is_inflow


# --------------------------------------------------------------------------
# Value-object semantics
# --------------------------------------------------------------------------


def test_is_immutable() -> None:
    r = make()
    with pytest.raises(FrozenInstanceError):
        r.external_id = "ORD-999"  # type: ignore[misc]


def test_is_hashable_despite_carrying_a_raw_dict() -> None:
    """`raw` is compare=False, so it is excluded from __eq__ and __hash__."""
    r = make(raw={"row": 1})
    assert len({r, make(raw={"row": 1})}) == 1


def test_raw_does_not_affect_equality() -> None:
    """Two parses of the same logical row are equal regardless of source noise."""
    assert make(raw={"a": 1}) == make(raw={"b": 2})


def test_raw_is_hidden_from_repr() -> None:
    assert "secret" not in repr(make(raw={"secret": "value"}))


def test_differing_fields_break_equality() -> None:
    assert make(paise=100) != make(paise=200)
    assert make(external_id="ORD-1") != make(external_id="ORD-2")


# --------------------------------------------------------------------------
# Defaults and refs
# --------------------------------------------------------------------------


def test_defaults() -> None:
    r = make()
    assert r.narration == ""
    assert r.refs == frozenset()
    assert r.raw == {}


def test_each_record_gets_its_own_raw_dict() -> None:
    a, b = make(), make()
    a.raw["mutated"] = True
    assert b.raw == {}


def test_refs_is_a_set_of_every_id_shaped_token() -> None:
    """§3.4: not a single typed field. L1 joins without knowing which column
    carried the key."""
    r = make(refs=frozenset({"SETL-88", "UTR-77291"}))
    assert "SETL-88" in r.refs
    assert "UTR-77291" in r.refs


# --------------------------------------------------------------------------
# Enums are string-safe (the StrEnum deviation from §3.4)
# --------------------------------------------------------------------------


def test_enums_render_as_their_values_in_f_strings() -> None:
    """With `(str, Enum)` these would render as "Source.BANK" and
    "Direction.INFLOW", which would land in posted journal narrations."""
    assert f"{Source.BANK}" == "bank"
    assert f"{Direction.INFLOW}" == "inflow"
    assert str(Source.LEDGER) == "ledger"


def test_enums_compare_equal_to_their_string_values() -> None:
    assert Source.BANK == "bank"
    assert Direction.OUTFLOW == "outflow"


def test_all_sources_and_directions() -> None:
    assert {s.value for s in Source} == {"ledger", "settlement", "bank"}
    assert {d.value for d in Direction} == {"inflow", "outflow"}


def test_str_is_readable() -> None:
    shown = str(make(source=Source.BANK, external_id="UTR-77291", paise=781120))
    assert "bank:UTR-77291" in shown
    assert "₹7,811.20" in shown
