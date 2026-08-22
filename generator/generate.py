"""Synthetic dataset generator. Guide §6. Gate 2.

    python -m generator.generate --seed 42 --scale 50      # clears the 50-record bar
    python -m generator.generate --seed 42 --scale 5000    # the demo run
    python -m generator.generate --seed 7  --scale 500     # held-out seed

Runs the real money flow (§1.3) FORWARD and records the answer. The agent runs
it BACKWARD and must rediscover it.

Build order inside this module (§6.2):
  1. Create truth first, files second.
  2. Replay the money flow forward: batch by capture date, T+2, fee, GST.
     The batch order_ids list IS the answer key.
  3. Inject failures, labelling each as you go. One injector per exception type.
  4. Write the files, hiding what real files hide.
  5. Emit truth.json.

Self-validating (§6.3): after writing, assert every truth mapping resolves to
real rows, every planted exception is present in the files, and totals tie. A
generator bug that produces an unsolvable dataset looks IDENTICAL to a matcher bug.

Same seed + same scale => byte-identical output. Verified by diff, not by claim.
"""
