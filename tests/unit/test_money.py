"""Money — the type that makes float money unrepresentable. Guide §5.1, §2.7 rule 1."""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.money import Money, MoneyParseError

# --------------------------------------------------------------------------
# The gate 1 requirement: it must be impossible to put a float in.
# --------------------------------------------------------------------------


def test_float_is_rejected() -> None:
    with pytest.raises(TypeError, match="never float"):
        Money(10.5)


def test_whole_float_is_rejected_too() -> None:
    """10.0 is still a float. Accepting it would open the door to 0.1 + 0.2."""
    with pytest.raises(TypeError):
        Money(10.0)


@pytest.mark.parametrize("bad", ["100", None, [100], {"paise": 100}, 10.5])
def test_non_int_types_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        Money(bad)  # type: ignore[arg-type]


def test_bool_is_rejected() -> None:
    """bool subclasses int, so True would silently become 1 paise."""
    with pytest.raises(TypeError):
        Money(True)  # type: ignore[arg-type]


def test_int_is_accepted() -> None:
    assert Money(123456).paise == 123456


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("₹1,234.56", 123456),  # the gate 1 example
        ("1234.56", 123456),
        ("₹0.01", 1),
        ("0", 0),
        ("₹8,000", 800000),  # no decimal part
        ("7811.20", 781120),
        ("₹ 1,23,456.78", 12345678),  # Indian lakh grouping
        (" 1234.56 ", 123456),
        ("Rs.1,234.56", 123456),
        ("INR 1234.56", 123456),
        ("1234.5", 123450),  # one decimal place is tenths, not hundredths
        (".50", 50),
        ("-12.50", -1250),
        ("\u221212.50", -1250),  # unicode minus
        ("(12.50)", -1250),  # accounting parentheses
        ("+12.50", 1250),
    ],
)
def test_from_rupee_string(text: str, paise: int) -> None:
    assert Money.from_rupee_string(text) == Money(paise)


def test_negative_parsing_is_not_off_by_the_fraction() -> None:
    """The naive `int(rupees)*100 + int(frac)` gets this wrong.

    "-12.50" partitions to rupees="-12", frac="50", giving -1200 + 50 = -1150.
    Stripping the sign first and applying it to the total gives -1250.
    """
    assert Money.from_rupee_string("-12.50").paise == -1250
    assert Money.from_rupee_string("-0.01").paise == -1


@pytest.mark.parametrize(
    "bad",
    [
        "1234.567",  # sub-paise precision — refuse rather than truncate
        "abc",
        "",
        "   ",
        "₹",
        "12.",
        "1,2x4.00",
        "--12.50",
        "12.5.6",
    ],
)
def test_ambiguous_strings_are_rejected(bad: str) -> None:
    """§4.0: reject what cannot be read; never guess, never silently repair."""
    with pytest.raises(MoneyParseError):
        Money.from_rupee_string(bad)


def test_parse_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        Money.from_rupee_string(1234)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Arithmetic and ordering
# --------------------------------------------------------------------------


def test_addition_and_subtraction() -> None:
    assert Money(100) + Money(50) == Money(150)
    assert Money(100) - Money(150) == Money(-50)


def test_arithmetic_stays_exact_where_float_would_drift() -> None:
    """The canonical float failure, in the domain it would corrupt."""
    total = Money.from_rupee_string("0.10") + Money.from_rupee_string("0.20")
    assert total == Money.from_rupee_string("0.30")
    assert total.paise == 30


def test_summing_many_small_amounts_is_exact() -> None:
    assert sum((Money(1) for _ in range(1000)), Money.zero()) == Money(1000)


def test_arithmetic_with_a_bare_int_is_refused() -> None:
    with pytest.raises(TypeError):
        Money(100) + 50  # type: ignore[operator]


def test_negation_and_absolute() -> None:
    assert -Money(100) == Money(-100)
    assert abs(Money(-100)) == Money(100)


def test_ordering() -> None:
    assert Money(100) < Money(200)
    assert max(Money(1), Money(999), Money(50)) == Money(999)
    assert sorted([Money(3), Money(1), Money(2)]) == [Money(1), Money(2), Money(3)]


def test_truthiness_is_about_zero_not_presence() -> None:
    assert not Money.zero()
    assert Money(1)
    assert Money(-1)


# --------------------------------------------------------------------------
# Value-object semantics
# --------------------------------------------------------------------------


def test_is_immutable() -> None:
    m = Money(100)
    with pytest.raises(FrozenInstanceError):
        m.paise = 200  # type: ignore[misc]


def test_is_hashable_and_value_compared() -> None:
    assert Money(100) == Money(100)
    assert len({Money(100), Money(100), Money(200)}) == 2


def test_survives_a_pickle_round_trip() -> None:
    assert pickle.loads(pickle.dumps(Money(123456))) == Money(123456)


# --------------------------------------------------------------------------
# Display — the only place rupees exist
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (123456, "₹1,234.56"),
        (781120, "₹7,811.20"),
        (0, "₹0.00"),
        (1, "₹0.01"),
        (100, "₹1.00"),
        (-1250, "-₹12.50"),
        (800000, "₹8,000.00"),
    ],
)
def test_str(paise: int, shown: str) -> None:
    assert str(Money(paise)) == shown


def test_display_is_exact_at_magnitudes_where_float_loses_precision() -> None:
    """Formatting via `paise / 100` would start rounding here; integers do not."""
    big = Money(9_007_199_254_740_993)  # 2**53 + 1 paise
    assert str(big).endswith(".93")
    assert Money.from_rupee_string(str(big).replace("₹", "")) == big


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_str_round_trips_through_the_parser(paise: int) -> None:
    """Anything Money can display, Money can read back exactly."""
    assert Money.from_rupee_string(str(Money(paise))) == Money(paise)


@given(
    st.integers(min_value=-10**9, max_value=10**9),
    st.integers(min_value=-10**9, max_value=10**9),
)
def test_addition_is_commutative_and_reversible(a: int, b: int) -> None:
    assert Money(a) + Money(b) == Money(b) + Money(a)
    assert (Money(a) + Money(b)) - Money(b) == Money(a)
