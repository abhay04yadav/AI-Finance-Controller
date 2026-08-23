"""SourceAdapter protocol. Guide §5.2, §5.3.

One narrow interface, one method (ISP). Source quirks die at the boundary:
adding a second gateway's report format, or a new bank's CSV layout, means
writing one adapter and touching zero downstream code (§5.9 scenario 3).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from core.models import Source

if TYPE_CHECKING:
    from ingest.normalizer import IngestResult


class SourceAdapter(Protocol):
    """Turn one file into canonical Records, plus whatever could not be read."""

    source: Source
    #: The file this adapter expects inside a dataset directory.
    filename: str

    def load(self, path: Path) -> IngestResult: ...
