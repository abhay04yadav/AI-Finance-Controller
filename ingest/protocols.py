"""SourceAdapter protocol. Guide §5.2.

One narrow interface, one method (ISP). Adding a new bank's CSV format touches
one adapter file and zero downstream code (§5.3, §5.9 scenario 3).
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from core.models import Source


class SourceAdapter(Protocol):
    source: Source

    def load(self, path: Path) -> Iterable["object"]:  # -> Iterable[Record] at Gate 4
        ...
