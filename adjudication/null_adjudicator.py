"""NullAdjudicator — the --no-llm path. Null Object pattern. Guide §4.4, §5.3.

Returns "no verdict" for every ambiguity, sending them all to exception. Zero
conditionals in the orchestrator.

This is a FIRST-CLASS SUPPORTED MODE, not a debug flag. `--no-llm` must produce
a working system at ~94% match rate and >= 98% precision (gate 10). It is both
an engineering safeguard and the strongest single line in the demo:
"AI is our last mile, not our crutch."
"""
