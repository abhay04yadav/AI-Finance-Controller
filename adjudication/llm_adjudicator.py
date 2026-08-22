"""LLM adjudication. Guide §4.4. GATE 11 — DO NOT IMPLEMENT BEFORE GATE 10 PASSES.

Two jobs, one layer:
  Job A — many candidates: SELECT using narration / settlement_id / capture dates,
          the three signals invisible to arithmetic.
  Job B — zero candidates: CLASSIFY + hypothesise + suggest an action. This
          becomes the WHY and ACTION on the exception card (§8.2). This is where
          the LLM earns its place: code can rank near-misses, only the model can
          articulate why Rs 80 is missing in language a controller can act on.

Hard budget: < 10% of records reach L4 (§2.2). Instrument and assert it.
Temperature 0. Prompt file versioned, version logged with every verdict.
Cache on a hash of the serialized ambiguity so reruns cost nothing and stay
identical. Batch ambiguities into one request — never one call per row.
"""
