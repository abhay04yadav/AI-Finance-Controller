"""Per-stage debug + profiling CLI. Guide §9.6.

    python -m pipeline.debug --stage L0 --dataset data/seed42     (gate 4)
    python -m pipeline.debug --stage L2 --dataset data/seed42     (gate 6)
    python -m pipeline.debug --profile --dataset data/seed42      (gate 10)

--profile prints time and record count for each of L0-L5. When throughput is a
judged metric you need to know which layer is slow — and being able to say
"L3 is 4ms, L4 is 380ms, that's why we keep L4 under 10%" is a strong answer.

Structured logs carry run_id on every line.
"""
