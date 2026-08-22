"""Verdict verification. Guide §4.4. Gate 11.

    LLM SELECTS. CODE VERIFIES. ALWAYS.

Three checks:
  1. hallucinated_candidate — verdict.selected is not among the candidate ids
  2. arithmetic_mismatch    — the chosen candidate is not actually a solution
  3. missing_reason         — no human-readable justification

A rejected verdict does NOT retry blindly. It falls through to an exception with
reason ADJUDICATION_REJECTED — itself an honest line for the exception list.
"""
