"""L4 — LLM Adjudication. Guide §4.4. GATE 11 of 14 — deliberately last.

NOTHING in this package may be implemented before gate 10 passes. Build the LLM
first and you lean on it, and the deterministic core stays weak. Build it last
and you end up with a system that works without AI — more robust, better story.

The pre-gate-11 check (Review Guide part 1) must return nothing:
    grep -rn "anthropic|api.anthropic|messages.create" --include=*.py . | grep -v adjudication/
"""
