"""MatchStrategy protocol. Guide §5.2, §5.4 (Liskov).

Every strategy returns list[MatchProposal] — an EMPTY LIST for "no opinion",
never None, never a raised exception. Any strategy can be swapped for another
without the caller changing behaviour.
"""

from typing import Protocol


class MatchStrategy(Protocol):
    name: str

    def propose(self, ctx: "object") -> list["object"]:  # MatchContext -> list[MatchProposal]
        ...
