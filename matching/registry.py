"""Strategy registration. Registry + Factory. Guide §5.3, §5.9.

Strategies self-register with a priority and the orchestrator runs them in that
order, each consuming the residual of the last (§5.4). Enabling or disabling a
layer is a config change, not a code change, and adding a chargeback matcher is
one new class plus one line here — zero existing logic modified (§5.9).
"""

from __future__ import annotations

from matching.protocols import MatchStrategy

#: Lower runs first. L1 must precede everything: it is the only layer allowed to
#: claim certainty, and every later layer is cheaper for what it leaves behind.
PRIORITIES = {
    "L1_exact": 10,
    "L3_subset": 30,
}


def build_strategies(*, no_fee_model: bool = False) -> list[MatchStrategy]:
    """The active matching chain, in execution order.

    Layers appear here as their gates land. `no_fee_model` is accepted now so
    the ablation flag is stable from gate 3 onward (§7.5); it takes effect once
    L2 exists.
    """
    from matching.exact_matcher import ExactMatcher
    from matching.subset_matcher import SubsetMatcher

    strategies: list[MatchStrategy] = [ExactMatcher(), SubsetMatcher()]
    return sorted(strategies, key=lambda s: PRIORITIES.get(s.name, 99))
