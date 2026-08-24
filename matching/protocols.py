"""Matching contracts: the strategy interface and the shared read model.
Guide §5.2, §5.4.

Every strategy returns `list[MatchProposal]` — an EMPTY LIST for "no opinion",
never None, never a raised exception (§5.4, Liskov). Any strategy can be swapped
for another without the caller changing behaviour.

`MatchContext` is the read model passed down the chain: indexed lookups, the
calendar, and a `flag()` sink for exceptions. It is read-only to strategies
except through `flag()` and `accept()`, so a matcher cannot quietly mutate the
world another matcher is about to read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from core.config import Settings
from core.dates import BusinessCalendar
from core.models import MatchProposal, Record, Source
from core.reason_codes import ReasonCode
from matching.fee_model import FeeModel


@dataclass(frozen=True, slots=True)
class Flag:
    """Something a strategy could not resolve, and says so."""

    reason_code: ReasonCode
    ref: str
    what: str = ""
    why: str = ""
    amount_paise: int | None = None
    raised_by: str = ""


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """A credit that several combinations explain equally well.

    Arithmetic has done all it can; separating these needs the narration, the
    settlement batch or the capture timing — the signals L4 reasons over
    (§4.4 job A). Recorded here so L4 has its input ready at gate 11.
    """

    credit_utr: str
    credit_paise: int
    narration: str
    options: tuple[tuple[str, ...], ...]
    target_paise: int


@dataclass
class MatchContext:
    """Indexed, read-only view of one dataset, plus the residual as it shrinks."""

    records: tuple[Record, ...]
    calendar: BusinessCalendar
    settings: Settings

    ledger_by_id: dict[str, Record] = field(default_factory=dict)
    bank_by_utr: dict[str, list[Record]] = field(default_factory=dict)
    settlements: tuple[Record, ...] = ()
    settlement_by_id: dict[str, Record] = field(default_factory=dict)
    utr_counts: Counter[str] = field(default_factory=Counter)

    #: Derived after each layer from what has been confirmed so far (§5.4).
    #: None until L2 has run, or when the --no-fee-model ablation is active.
    fee_model: FeeModel | None = None
    fee_model_enabled: bool = True

    accepted: list[MatchProposal] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    _claimed_bank: set[str] = field(default_factory=set)
    _claimed_ledger: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        records: tuple[Record, ...],
        *,
        calendar: BusinessCalendar,
        settings: Settings,
    ) -> MatchContext:
        ctx = cls(records=records, calendar=calendar, settings=settings)
        for record in records:
            if record.source is Source.LEDGER:
                ctx.ledger_by_id[record.external_id] = record
            elif record.source is Source.BANK:
                ctx.bank_by_utr.setdefault(record.external_id, []).append(record)
        ctx.settlements = tuple(
            r for r in records if r.source is Source.SETTLEMENT
        )
        ctx.settlement_by_id = {r.external_id: r for r in ctx.settlements}
        # Counted once, up front: a repeated UTR is the DUPLICATE_UTR signal and
        # every strategy needs to see it before it matches anything (§4.1 step 5).
        ctx.utr_counts = Counter(
            r.external_id for r in records if r.source is Source.BANK
        )
        return ctx

    # ------------------------------------------------------------- residual

    def is_bank_claimed(self, utr: str) -> bool:
        return utr in self._claimed_bank

    def is_ledger_claimed(self, order_id: str) -> bool:
        return order_id in self._claimed_ledger

    def open_bank_credits(self) -> list[Record]:
        """Credits no strategy has explained yet, in deterministic order."""
        seen: set[str] = set()
        out: list[Record] = []
        for utr in sorted(self.bank_by_utr):
            if utr in self._claimed_bank:
                continue
            for record in self.bank_by_utr[utr]:
                if record.external_id not in seen:
                    seen.add(record.external_id)
                    out.append(record)
        return out

    def open_ledger_rows(self) -> list[Record]:
        return [
            self.ledger_by_id[oid]
            for oid in sorted(self.ledger_by_id)
            if oid not in self._claimed_ledger
        ]

    # ------------------------------------------------------------- mutation

    def accept(self, proposal: MatchProposal) -> None:
        """Take a proposal and remove its records from the residual."""
        self.accepted.append(proposal)
        self._claimed_bank.add(proposal.bank_utr)
        self._claimed_ledger.update(proposal.ledger_ids)

    def confirmed_fee_pairs(self) -> list[tuple[int, int]]:
        """(gross, net) from matches we are certain about — L2's only input.

        Drawn from accepted proposals rather than from every settlement in the
        file, because a settlement nobody could match may be unmatched precisely
        because its money does not add up, and feeding that to the fee model
        would teach it the wrong rate.
        """
        pairs: list[tuple[int, int]] = []
        for proposal in self.accepted:
            if proposal.settlement_id is None:
                continue
            record = self.settlement_by_id.get(proposal.settlement_id)
            if record is None:
                continue
            detail = record.settlement()
            # Belt and braces: an unitemised deduction would read as extra fee.
            if detail.unitemised_paise == 0 and detail.gross.paise > 0:
                pairs.append((detail.gross.paise, detail.net.paise))
        return pairs

    def refresh_derived(self) -> None:
        """Recompute what later layers depend on. Called after each strategy.

        L2 is not a matching strategy — it produces no proposals. It is a model
        derived from what the previous layers established, which is why it lives
        here rather than in the chain (§5.4).
        """
        if not self.fee_model_enabled:
            self.fee_model = FeeModel.disabled()
            return
        pairs = self.confirmed_fee_pairs()
        if pairs:
            self.fee_model = FeeModel.infer(pairs, gst_rate=self.settings.gst_rate)

    def record_ambiguity(
        self, credit: Record, options: list[list[str]], target_paise: int
    ) -> None:
        self.ambiguities.append(
            Ambiguity(
                credit_utr=credit.external_id,
                credit_paise=credit.amount.paise,
                narration=credit.narration,
                options=tuple(tuple(o) for o in options),
                target_paise=target_paise,
            )
        )

    def flag(
        self,
        reason_code: ReasonCode,
        ref: str,
        *,
        what: str = "",
        why: str = "",
        amount_paise: int | None = None,
        raised_by: str = "",
    ) -> None:
        self.flags.append(
            Flag(reason_code, ref, what, why, amount_paise, raised_by)
        )


class MatchStrategy(Protocol):
    """One matching layer. Returns proposals; never mutates beyond ctx.flag()."""

    name: str

    def propose(self, ctx: MatchContext) -> list[MatchProposal]: ...
