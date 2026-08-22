"""Money value object — integer paise, always. Guide §2.7 rule 1, §5.1.

Gate 1 fills this in. The invariant that must hold once implemented:
`Money(10.5)` raises TypeError. It must be impossible to put a float in.
Convert to rupees only at the display boundary.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """Amount in integer paise. Never float, never Decimal in transit."""

    paise: int

    def __post_init__(self) -> None:
        raise NotImplementedError("Gate 1 — §5.1")

    @classmethod
    def from_rupee_string(cls, s: str) -> "Money":
        """Parse '₹1,234.56' -> Money(123456). Parses straight to paise, never via float."""
        raise NotImplementedError("Gate 1 — §5.1")
