"""Money value object — integer paise, always. Guide §2.7 rule 1, §5.1.

Primitive obsession is the root of money bugs. `0.1 + 0.2 != 0.3` will silently
corrupt a reconciliation and cost hours to find, so this type makes float money
*unrepresentable* rather than merely discouraged:

    >>> Money(10.5)
    TypeError: Money must be integer paise — never float

Rupees exist only at the display boundary (`__str__`). Everywhere else — the
solver, the fee model, the journal — the value is an int.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

PAISE_PER_RUPEE: Final = 100

# Currency symbols and unit markers stripped before parsing.
# "\u00a0" is a non-breaking space, which spreadsheet exports emit between
# the currency symbol and the digits. Written as an escape so it stays visible.
_STRIP: Final = ("₹", "Rs.", "RS.", "Rs", "INR", ",", " ", "\u00a0")


class MoneyParseError(ValueError):
    """A money string could not be parsed unambiguously.

    Per §4.0, ingest rejects what it cannot read rather than guessing. A row
    that raises this becomes an INGEST_ERROR on the exception page; it is never
    silently repaired.
    """


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An amount in integer paise. Immutable, ordered, hashable."""

    paise: int

    def __post_init__(self) -> None:
        # bool is a subclass of int, so `Money(True)` would otherwise pass this
        # check and silently become 1 paise.
        if isinstance(self.paise, bool) or not isinstance(self.paise, int):
            raise TypeError(
                f"Money must be integer paise — never {type(self.paise).__name__}"
            )

    # ---------------------------------------------------------------- parsing

    @classmethod
    def from_rupee_string(cls, s: str) -> Money:
        """Parse a rupee-denominated string into exact paise.

            >>> Money.from_rupee_string("₹1,234.56")
            Money(paise=123456)

        Handles the sign forms that appear in real exports — a leading `-`, a
        Unicode minus, and accounting parentheses — because the naive
        `int(rupees) * 100 + int(frac)` formulation gets negatives wrong:
        "-12.50" would parse as -1200 + 50 = -1150 instead of -1250. The sign is
        therefore stripped first and applied to the total.

        Raises MoneyParseError on anything ambiguous, including more than two
        decimal places. Silently truncating sub-paise precision is exactly the
        class of bug this type exists to prevent.
        """
        if not isinstance(s, str):
            raise TypeError(f"expected a string, got {type(s).__name__}")

        cleaned = s.strip()
        for token in _STRIP:
            cleaned = cleaned.replace(token, "")
        cleaned = cleaned.strip()

        negative = False
        if cleaned.startswith("(") and cleaned.endswith(")"):
            negative, cleaned = True, cleaned[1:-1].strip()
        if cleaned.startswith(("-", "\u2212")):  # ASCII hyphen or Unicode minus
            negative, cleaned = True, cleaned[1:].strip()
        elif cleaned.startswith("+"):
            cleaned = cleaned[1:].strip()

        if not cleaned:
            raise MoneyParseError(f"no amount found in {s!r}")

        rupees, dot, frac = cleaned.partition(".")
        rupees = rupees or "0"

        if not rupees.isdigit():
            raise MoneyParseError(f"not a valid amount: {s!r}")
        if dot and not frac.isdigit():
            raise MoneyParseError(f"malformed decimal part in {s!r}")
        if len(frac) > 2:
            raise MoneyParseError(
                f"sub-paise precision in {s!r} — refusing to truncate silently"
            )

        total = int(rupees) * PAISE_PER_RUPEE + int((frac + "00")[:2])
        return cls(-total if negative else total)

    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    # ------------------------------------------------------------- arithmetic

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise + other.paise)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise - other.paise)

    def __neg__(self) -> Money:
        return Money(-self.paise)

    def __abs__(self) -> Money:
        return Money(abs(self.paise))

    def __bool__(self) -> bool:
        return self.paise != 0

    # ---------------------------------------------------------------- display

    def __str__(self) -> str:
        """Format for humans. The only place rupees appear.

        Formatted from integers rather than `paise / 100` so that no float ever
        touches a monetary value, not even for display.
        """
        sign = "-" if self.paise < 0 else ""
        rupees, paise = divmod(abs(self.paise), PAISE_PER_RUPEE)
        return f"{sign}₹{rupees:,}.{paise:02d}"
