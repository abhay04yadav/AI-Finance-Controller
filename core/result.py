"""Result[T, E] — explicit outcomes, no bare exceptions. Guide §5.5.

Expected failures are VALUES: "no candidates found" is Ok([]), "verdict failed
guardrails" is Err("arithmetic_mismatch"). These flow to the exception list.

Exceptions are reserved for BUGS: unbalanced journal entry, float money,
unknown source -> raise, fail loudly, fix the code.

Never swallow. `except: pass` in a reconciliation system is how money
disappears — ruff rule E722 is enabled in pyproject.toml to catch it.
"""

# Ok[T] | Err[E] — Gate 1, §5.5.
