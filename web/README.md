# web/ — Next.js App Router + Tailwind

**Not scaffolded until gate 12.** Guide §8.

Deliberate: `create-next-app` adds tens of thousands of files that would be
noise through gates 0-11, and the UI has nothing to render until L5 posts
entries. The directory shape is reserved here so the structure matches §3.2.

## Screens (§8.4)

| Route | Role |
|---|---|
| `/exceptions` | **HOME.** Sorted by amount desc. Filter by reason code. Header: `8 open · ₹8,400 unreconciled` |
| `/review` | Confidence 0.70-0.95. Prepared entry + Approve/Reject. 2 seconds per item. |
| `/books` | Books-closed summary + cash position (§1.6). The "we closed the loop" screen. |
| `/benchmark` | Live eval run. THE judge screen. |

## The inversion (§8.1)

The exception list is the home screen, not a red box at the bottom of a green
dashboard. A controller never looks at matched rows — those are done. Building
for the 88% that needs no attention is building for nobody.

If landing on the home page shows a green "45/50 matched" dashboard, §8.1 was
not understood.

## Non-negotiables

- Every exception card carries WHAT / WHY / ACTION (§8.2).
- Review cards show the **prepared journal entry**, not just the suggested
  match. That is what makes it a 2-second decision instead of a 2-minute
  investigation.
- Action buttons are generated from `is_available()`, never hardcoded (§8.3).
- `AWAITING_SETTLEMENT` gets its own visual treatment. It is **not an error** —
  the money is genuinely in transit (Appendix A).
- `font-variant-numeric: tabular-nums` on all figures. Misaligned decimals in a
  finance tool read as amateur immediately (§8.5).
- The signature element: the **reconciliation trace**, drawn as a connected
  trail — `ORD-101 → SETL-88 → UTR-77291 → bank credit` (§8.5).
- Benchmark screen must actually run. Hardcoded or cached numbers are the gate
  13 stop condition.
