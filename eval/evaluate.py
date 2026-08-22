"""Scoring against planted ground truth. Guide §7.3. Gate 3.

EXACT-SET SEMANTICS. The line that decides the submission:

    set(m.ledger_ids) == set(truth["mappings"].get(utr, []))

Two of three orders correct is WRONG, not 67% right. There is no partial credit
in reconciliation: a half-matched settlement posts a wrong journal entry. Any
fuzzy comparison or overlap ratio inflates precision and makes it meaningless.

Match rate vs match precision (§7.2):
    match rate      = attempted / total          coverage
    match precision = correct   / attempted      THE NUMBER THAT MATTERS

A team that answers 95 and gets 94 right beats a team that answers 100 and gets
82 right, however much better the second dashboard looks. In finance a wrong
answer is worse than no answer.

Flags: --no-llm (ablation), --no-fee-model (quantify L2), --seed, --seeds, --scale.
"""
