# web/ — Next.js App Router

Built at gate 12. Guide §8.

```bash
make api      # :8000  the run, the books, the actions
make web      # :3000  the screens
```

Two processes. `next.config.mjs` proxies `/api/*` to the FastAPI server, so the
browser sees one origin and no CORS preflight on every button press.

## Which seed the screens show

`AFC_SEED` picks the dataset the API reconciles (default 42). `?seed=N` on any
route overrides it for that request — the run is built on first ask and kept, so
`/exceptions?seed=7` reconciles seed 7 once and then serves it.

That is how gate 12 is verified: open the app on 42, open it on 7, and **every
figure on every screen must change.** Anything that survives is hardcoded.

Three figures legitimately do not move, and each is a property of something
other than the dataset:

| Unchanged | Why |
|---|---|
| 605 records processed | `scale` fixes how many rows the generator writes; the seed fixes what is in them |
| 27 planted anomalies | same — one injector run per failure mode per scale |
| Rounding write-off ₹0.00 | L1 posts from the gateway's *stated* fee, so nothing is left to write off |

## Dependencies

`next`, `react`, `react-dom`. That is the whole list.

**No Tailwind**, deliberately. The design in `docs/AI Finance Controller
wireframes` is exact hex values and exact pixel sizes; a utility framework can
only express those as arbitrary values, and `text-[#191713] text-[13.5px]` is
Tailwind in name only — strictly harder to read than the CSS it compiles to.
`app/globals.css` holds the tokens, lifted verbatim from the wireframe.

No charting library either: the two diagrams are hand-written SVG driven by API
data, which is what §8.5 requires of the trace.

## Screens

| Route | Frame | Role |
|---|---|---|
| `/exceptions` | 2a, 3a | **HOME.** Ledger rows, running open balance, trace, actions |
| `/review` | 2b | Prepared journal entry + Approve/Reject |
| `/books` | 2c, 3b | Disposition, tie-out, cash position, close |
| `/benchmark` | 2d | Live eval run |

`/` redirects to `/exceptions`. Not a landing page, not a summary with the
worklist linked from it — the inversion §8.1 asks for is that the worklist **is**
the index.

## Non-negotiables, and where each one lives

- **WHAT / WHY / ACTION on every card** — `app/exceptions/page.tsx`. `why` is
  rendered exactly as the API returns it: no fallback text, no templating. The
  card labels it a hypothesis only when `why_source` says `model`.
- **Actions from `available_for()`** — the payload carries them; the frontend
  has no action list of its own. If the wireframe offers a button the registry
  does not return for that reason code, the registry wins and the button is not
  there.
- **In-transit below a rule, in its own collection** — never in the list, never
  in the header count, never blocking a close. Its actions all post nothing.
- **The open balance is a computed running balance** — server-side, in
  `api/routes/exceptions.py`, descending to `Cleared 0.00`. The API also returns
  `balance_ties`; if it is ever false the page prints the discrepancy instead of
  the column.
- **`tabular-nums` everywhere, Indian grouping everywhere** — set on `:root` and
  never switched off; `lib/money.ts` formats via `Intl.NumberFormat("en-IN")`,
  so ₹1,50,918.37 and never ₹150,918.37.
- **The trace renders from real match data** — `components/Trace.tsx` owns
  geometry and nothing else. It does not know what a fee is or when T+2 falls;
  nodes, arithmetic steps and the residual all arrive as data.
- **Review balance is enforced server-side** — the button disables on an
  unbalanced entry, but `POST /api/review/{utr}/approve` refuses it in the
  handler and would refuse a caller who bypassed the page entirely.
- **Benchmark runs live** — `POST /api/benchmark` every press. The fingerprint is
  on screen so a judge can run it twice and check §9.1 themselves.
