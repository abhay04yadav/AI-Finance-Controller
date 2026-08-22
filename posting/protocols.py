"""Poster protocol. Guide §5.2."""

from typing import Protocol


class Poster(Protocol):
    def post(self, match: "object") -> "object":  # -> Result[JournalEntry, str]
        ...
