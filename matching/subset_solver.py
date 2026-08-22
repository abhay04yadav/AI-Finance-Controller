"""Pure subset-sum arithmetic. Integers in, integers out. Guide §4.3c, §5.4 (SRP).

This file knows NOTHING about payments — no fees, no dates, no records. That is
what makes it property-testable with hypothesis (§9.7).

Two constraints from the guide:
  - The pool contains refunds as NEGATIVE values. Textbook subset-sum DP assumes
    non-negative values and will silently miss every refund case. Use DFS with
    pruning for small pools, meet-in-the-middle for larger ones.
  - Do NOT stop at the first solution. Collect up to K=5 (§4.3d). Multiple
    solutions is information — it is the signal that routes a case to L4.
    Returning early hides ambiguity and manufactures false confidence.

Pools too wide to solve return [] — emit UNRESOLVED, never hang.
"""
