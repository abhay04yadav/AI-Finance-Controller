"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Bar, Legend, ramp } from "@/components/Bar";
import { Gloss } from "@/components/Glossary";
import { Tour } from "@/components/Tour";
import { TraceDiagram } from "@/components/Trace";
import {
  api,
  ApiError,
  type ActionOffer,
  type ExceptionCard,
  type ExceptionsPayload,
} from "@/lib/api";
import { paise, pct, rupees, shortDate, stamp } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * /exceptions — THE HOME SCREEN. Guide §8.1, P0 design 4a.
 *
 * The P0 pass is about ANSWERING THE ROW rather than presenting it. Four
 * changes carry it, and each one replaces something that made a controller
 * work to reach a fact the run already knew.
 *
 * **The funnel, before the failures.** The screen used to open on nine
 * unexplained credits with no denominator. It now opens on where all 605
 * records went, so "seven open" reads as the tail of a job that mostly
 * finished rather than as the whole picture.
 *
 * **A row leads with a title, not a code.** `AMBIGUOUS_UNADJUDICATED` is what
 * a developer greps for; "Several answers fit equally well" is what a
 * controller scans. Both are on the row, in that order of prominence, and
 * both come from the API — a screen must never be able to describe a code the
 * backend does not know.
 *
 * **The open card answers four questions in order.** What happened, what we
 * know, why we stopped, what we recommend — then, behind a click, how it was
 * decided layer by layer, including what the model was and was not allowed to
 * do. The old card put WHAT and WHY side by side and left the recommendation
 * implicit in the button order.
 *
 * **Every button says what it posts.** "Dr Bank 24,860.00 · Cr Suspense
 * 24,860.00" under the button, built from the action's own declared shape.
 * Pressing a button in a finance tool without knowing what it writes is the
 * thing this screen exists to stop.
 *
 * What has NOT changed: the open balance is still a computed running balance
 * descending to zero, in-transit still sits below a rule in its own
 * collection, and buttons still come from `available_for()`.
 */
export default function Page() {
  return (
    <div className="page-wide">
      <Suspense fallback={<div className="notice">loading…</div>}>
        <Exceptions />
      </Suspense>
    </div>
  );
}

type SortKey = "amount" | "age";

function Exceptions() {
  const seed = useSeed();
  const [data, setData] = useState<ExceptionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortKey>("amount");
  // null = nothing open, and that is how the page arrives. A ledger that
  // opens with a card already expanded answers a question nobody asked and
  // pushes the rest of the list below the fold.
  const [cursor, setCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tour, setTour] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.exceptions(seed));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [seed]);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Bring the opened card's top under the header.
   *
   * Opening a card swaps a ~90px row for a ~700px panel, so everything below
   * the cursor moves down and everything above a card that just CLOSED moves
   * up. The card you asked for therefore lands anywhere — sometimes above the
   * fold, sometimes past the bottom — and you have to go and find it. Walking
   * the list with j/k was the worst of it: every keystroke put the thing you
   * were reading somewhere new.
   *
   * The offset is measured off the sticky bar rather than hard-coded, because
   * that bar wraps to two lines on a narrow window and a fixed 44px would then
   * tuck the card's first line behind it.
   */
  const settled = useRef(false);
  useEffect(() => {
    // Not on arrival. Row 0 opens by default, and scrolling to it on load
    // would throw away the funnel and the totals before they were read.
    if (!settled.current) {
      settled.current = true;
      return;
    }
    // Closing a card leaves the reader where they were. Only an OPEN needs
    // the page to move.
    if (cursor === null) return;
    const panel = document.querySelector<HTMLElement>(".exc-panel");
    if (!panel) return;
    const bar = document.querySelector<HTMLElement>(".topbar");
    const offset = (bar?.getBoundingClientRect().height ?? 0) + 14;
    const top = panel.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({
      top: Math.max(top, 0),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }, [cursor]);

  const rows = useMemo(() => {
    const list = (data?.exceptions ?? []).filter(
      (e) => !filter || e.reason_code === filter,
    );
    // Sorting by age is not a second ordering of the same list — it is a
    // different question ("what has been sitting longest") and the answer is
    // a different row. Rows with no value date sort last either way, because
    // "unknown age" is not "brand new".
    if (sortBy === "age") {
      return [...list].sort(
        (a, b) => (b.age_days ?? -1) - (a.age_days ?? -1),
      );
    }
    return list;
  }, [data, filter, sortBy]);

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.target instanceof HTMLInputElement || tour) return;
      // From nothing open, j takes the top of the list and k the bottom.
      if (ev.key === "j") {
        setCursor((c) => (c === null ? 0 : Math.min(c + 1, rows.length - 1)));
      }
      if (ev.key === "k") {
        setCursor((c) =>
          c === null ? rows.length - 1 : Math.max(c - 1, 0),
        );
      }
      // Escape closes, which is the keyboard half of clicking an open row.
      if (ev.key === "Escape") setCursor(null);
      const n = Number.parseInt(ev.key, 10);
      if (n >= 1 && n <= 3 && cursor !== null) {
        const row = rows[cursor];
        const action = row?.actions[n - 1];
        if (row && action && !row.action_state) void act(row.ref, action.code);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function act(ref: string, code: string) {
    setBusy(ref);
    try {
      await api.act(ref, code, seed);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function undo(ref: string, code: string) {
    setBusy(ref);
    try {
      await api.undo(ref, code, seed);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error && !data) return <div className="notice notice-bad">{error}</div>;
  if (!data) return <div className="notice">reconciling…</div>;

  const openCount = data.open;

  return (
    <div className="card">
      {tour && <Tour onClose={() => setTour(false)} />}

      <FunnelBand data={data} />

      <div style={{ padding: "38px var(--page-pad) 0" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 44,
            flexWrap: "wrap",
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow">Unreconciled · action required</div>
            <div
              style={{
                marginTop: 14,
                display: "flex",
                alignItems: "baseline",
                gap: 16,
                flexWrap: "wrap",
              }}
            >
              <div className="hero">{rupees(data.unreconciled_paise)}</div>
              <div className="beside">
                across {openCount} exception{openCount === 1 ? "" : "s"}
              </div>
              {data.cleared_paise > 0 && (
                <div
                  style={{
                    font: "400 13px/1 var(--mono)",
                    color: "var(--green)",
                  }}
                >
                  ↓ {rupees(data.cleared_paise)} cleared just now
                </div>
              )}
            </div>

            {/* The sentence that says what the screen is, before any jargon. */}
            <div className="lead" style={{ marginTop: 16, maxWidth: "56ch" }}>
              Money that arrived but can&rsquo;t be explained yet. {openCount}{" "}
              credit{openCount === 1 ? "" : "s"} came in where no{" "}
              <Gloss term="recon">reconciliation</Gloss> of orders, fees and
              refunds adds up to what the bank actually paid.
            </div>
            <div
              style={{
                marginTop: 12,
                font: "400 13px/1.6 var(--mono)",
                color: "var(--ink-label)",
              }}
            >
              Run {stamp(data.started_at)} · {data.auto_posted} posted without
              review · {data.pending_review} awaiting approval
            </div>
          </div>

          <Triage onTour={() => setTour(true)} />
        </div>

        <Tiles data={data} />

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            gap: 24,
            marginTop: 26,
            flexWrap: "wrap",
          }}
        >
          <div className="chips">
            <button
              className="chip"
              aria-pressed={filter === null}
              onClick={() => setFilter(null)}
            >
              ALL {openCount}
            </button>
            {data.by_reason.map((r) => (
              <button
                key={r.reason_code}
                className="chip"
                aria-pressed={filter === r.reason_code}
                onClick={() =>
                  setFilter(filter === r.reason_code ? null : r.reason_code)
                }
              >
                {r.reason_code} <span className="n">{r.count}</span>
              </button>
            ))}
          </div>
          <div className="sortby">
            Sort by{" "}
            <button
              aria-pressed={sortBy === "amount"}
              onClick={() => setSortBy("amount")}
            >
              amount ↓
            </button>{" "}
            ·{" "}
            <button
              aria-pressed={sortBy === "age"}
              onClick={() => setSortBy("age")}
            >
              age
            </button>
          </div>
        </div>
      </div>

      {/* --------------------------------------------- composition chart */}
      {data.composition.length > 0 && (
        <div style={{ padding: "0 var(--page-pad)", marginTop: 30 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              gap: 16,
              flexWrap: "wrap",
            }}
          >
            <div className="label-sm">Composition by reason code</div>
            <div
              style={{
                font: "400 11px var(--mono)",
                color: "var(--ink-faint)",
              }}
            >
              {rupees(data.unreconciled_paise + data.cleared_paise)} across{" "}
              {data.by_reason.length} code
              {data.by_reason.length === 1 ? "" : "s"}
            </div>
          </div>
          <Bar
            className=""
            height={13}
            segments={data.composition.map((c, i) => ({
              share: c.share,
              color: ramp(i),
              label: c.reason_code,
              title: `${c.reason_code} ${paise(c.paise)}`,
            }))}
          />
          <Legend
            items={data.by_reason.slice(0, 4).map((r) => ({
              label: r.count > 1 ? `${r.reason_code} ×${r.count}` : r.reason_code,
              color: ramp(
                data.composition.findIndex(
                  (c) => c.reason_code === r.reason_code,
                ),
              ),
              note: ` ${pct(r.share)}`,
            }))}
            trailing={
              data.by_reason.length > 4
                ? `+ ${data.by_reason.length - 4} more codes`
                : undefined
            }
          />
        </div>
      )}

      {error && <div className="notice notice-bad">{error}</div>}
      {!data.balance_ties && (
        <div className="notice notice-bad">
          The open-balance column does not tie: {paise(data.residual_paise)}{" "}
          paise left over. This is an aggregation bug, not a display one.
        </div>
      )}

      <div className="ledger-head" style={{ marginTop: 26 }}>
        <div>Exception</div>
        <div style={{ textAlign: "center" }}>Signal</div>
        <div style={{ textAlign: "right" }}>Amount</div>
        <div style={{ textAlign: "right" }}>Open balance</div>
      </div>

      <div className="ledger-body">
        {rows.map((row, i) => (
          <Row
            key={row.ref}
            row={row}
            /* One open at a time, or none. The cursor IS the disclosure
               state, so the two can never disagree and j/k walks the open
               card down the list. */
            expanded={i === cursor}
            busy={busy === row.ref}
            /* Clicking the open card closes it. Clicking a different row
               moves the disclosure there. */
            onFocus={() => setCursor((c) => (c === i ? null : i))}
            onAct={(code) => act(row.ref, code)}
            onUndo={(code) => undo(row.ref, code)}
          />
        ))}

        {/* The bottom of the descent. Every row above subtracted its amount
            from the open balance; this is where the column lands. */}
        <div className="cleared-row">
          <div className="cleared-label cleared-check">
            <Tick size={17} />
            Nothing left unexplained
          </div>
          <div />
          <div />
          <div className="balance-cleared" style={{ textAlign: "right" }}>
            {paise(0)}
          </div>
        </div>
      </div>

      <InTransit data={data} />
    </div>
  );
}

/* ------------------------------------------------ where every record went */

/** Which palette role each funnel segment takes. Blue is the deterministic
 *  core, purple is the model, gold is waiting on a person, red is open. */
const FUNNEL_COLOUR: Record<string, string> = {
  L1: "var(--blue)",
  L3: "var(--blue-light)",
  L4: "var(--purple)",
  REVIEW: "var(--gold)",
  OPEN: "var(--red)",
};

/**
 * The denominator, before the failures.
 *
 * Opening on seven unexplained credits with nothing to divide them by makes a
 * run that mostly worked look like a run that mostly did not. The segments
 * are disjoint and sum to the credit count — the API refuses to draw L3 and
 * the review queue as separate bands when they are the same eleven records.
 */
function FunnelBand({ data }: { data: ExceptionsPayload }) {
  if (data.funnel.length === 0) return null;
  const credits = data.funnel.reduce((n, s) => n + s.count, 0);

  return (
    <div className="funnel">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 24,
          flexWrap: "wrap",
        }}
      >
        <div className="label-sm">Where every record went</div>
        <div
          style={{
            font: "400 11px var(--mono)",
            color: "var(--ink-faint)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {data.scale} records in · {credits} bank credits
        </div>
      </div>

      <div className="funnel-bar">
        {data.funnel.map((s) => (
          <span
            key={s.key}
            title={`${s.label} · ${s.count} · ${s.note}`}
            style={{
              width: `${s.share * 100}%`,
              background: FUNNEL_COLOUR[s.key] ?? "var(--slate)",
            }}
          />
        ))}
      </div>

      <div className="funnel-legend">
        {data.funnel.map((s) => (
          <span key={s.key}>
            <span
              className="sw"
              style={{ background: FUNNEL_COLOUR[s.key] ?? "var(--slate)" }}
            />
            <span className="k">{s.label}</span>
            <span className="v">
              {s.count} credit{s.count === 1 ? "" : "s"}
            </span>
            {s.paise > 0 && <span className="note">{rupees(s.paise)}</span>}
            <span className="note">· {s.note}</span>
          </span>
        ))}
      </div>

      <div className="lead" style={{ marginTop: 16, maxWidth: "80ch" }}>
        {data.scale} records arrived across three files. The system explained{" "}
        {credits - data.open} of {credits} credits without asking. These{" "}
        {data.open} are what&rsquo;s left.
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- tiles */

/** Largest, oldest, most common, and how many need a person. */
function Tiles({ data }: { data: ExceptionsPayload }) {
  const h = data.highlights;
  if (!h.largest) return null;

  return (
    <div className="tiles">
      <div className="tile">
        <span className="label-sm">Largest</span>
        <span className="v">{rupees(h.largest.amount_paise)}</span>
        <span className="sub">{h.largest.reason_code}</span>
      </div>

      {h.oldest && (
        <div className="tile">
          <span className="label-sm">Oldest</span>
          <span className="v">
            {shortDate(h.oldest.value_date)}{" "}
            <span className="stale">· {h.oldest.age_days}d</span>
          </span>
          <span className="sub">{h.oldest.reason_code}</span>
        </div>
      )}

      {h.most_common && (
        <div className="tile">
          <span className="label-sm">Most common</span>
          <span className="v">
            {h.most_common.reason_code}{" "}
            <span className="small">×{h.most_common.count}</span>
          </span>
          {/* "tied with 2 other codes" is a different claim from "the most
              common code", and on a seven-row list the tie is the usual case. */}
          <span className="sub">
            {h.most_common.tied_with > 0
              ? `tied with ${h.most_common.tied_with} other code${
                  h.most_common.tied_with === 1 ? "" : "s"
                }`
              : "clear of the next code"}
          </span>
        </div>
      )}

      {h.needs_human && (
        <div className="tile">
          <span className="label-sm">Needs a human</span>
          <span className="v">
            {h.needs_human.count} of {h.needs_human.of}
          </span>
          <span className="sub">every open exception, by definition</span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------ keyboard triage */

function Triage({ onTour }: { onTour: () => void }) {
  return (
    <div className="triage">
      <div className="triage-head">Keyboard triage</div>
      <div className="triage-body">
        <div className="triage-row">
          <span className="triage-keys">
            <span className="cap">j</span>
            <span className="cap">k</span>
          </span>
          <span>move between rows</span>
        </div>
        <div className="triage-row">
          <span className="triage-keys">
            <span className="cap">1</span>
            <span style={{ color: "var(--ink-ghost)", fontSize: 10.5 }}>–</span>
            <span className="cap">3</span>
          </span>
          <span>run action 1–3</span>
        </div>
        <div className="triage-row">
          <span className="triage-keys">
            <span className="cap">S</span>
          </span>
          <span>
            snooze <span style={{ color: "var(--ink-faint)" }}>· in transit only</span>
          </span>
        </div>
      </div>
      <button type="button" className="triage-foot" onClick={onTour}>
        What am I looking at? · 4 steps →
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ row */

function Row({
  row,
  expanded,
  busy,
  onFocus,
  onAct,
  onUndo,
}: {
  row: ExceptionCard;
  expanded: boolean;
  busy: boolean;
  onFocus: () => void;
  onAct: (code: string) => void;
  onUndo: (code: string) => void;
}) {
  const [why, setWhy] = useState(false);
  const [how, setHow] = useState(false);
  const acted = row.action_state?.state === "acted";

  if (!expanded) return <ClosedRow row={row} acted={acted} onFocus={onFocus} />;

  const t = row.trace;
  const expected = t?.steps.find((s) => s.kind === "subtotal") ?? null;
  const residualStep = t?.steps.find((s) => s.kind === "residual") ?? null;
  const residual = t?.residual_paise ?? 0;

  return (
    <div className="row row-open">
      <div className={`exc-panel ${acted ? "exc-panel-done" : ""}`}>
        {/* The claim, then the proof, then the detail. The headline is the
            one sentence a controller has to read, so it gets the full width
            rather than 55% of it; the trace is the evidence for that
            sentence, so it comes next, before the prose that elaborates. */}
        <div className="exc-lead">
          {/* The identity strip is the close affordance — the same line you
                clicked to open it. The panel as a whole is NOT clickable:
                everything inside it is prose to read or a control to press,
                and a card that collapses when you click its own headline is
                a card you cannot read. */}
          <button
            type="button"
            className="exc-close"
            onClick={onFocus}
            aria-expanded="true"
            title="Close this card"
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 13,
              flexWrap: "wrap",
            }}
          >
            <span className="row-title">{row.title}</span>
            <span className="row-code">{row.reason_code}</span>
            <span className="row-ref">
              <Gloss term="UTR">{row.ref.split("-")[0]}</Gloss>
              {row.ref.slice(row.ref.indexOf("-"))}
              {row.value_date ? ` · value ${shortDate(row.value_date)}` : ""}
              {row.age_days !== null && (
                <span className="row-age"> · {row.age_days}d</span>
              )}
            </span>
          </button>

          <div className="exc-headline">
            {acted && row.action_state
              ? row.action_state.detail
              : row.headline}
          </div>

          {/* Three cases, not two. A row with no trace that nobody has acted
              on — an auto-refund explains itself — used to be told it had
              been acted on and its trace was in the audit trail, which was
              simply untrue. It now says nothing, because there is nothing
              to reconstruct. */}
          {acted ? (
            <div className="prose-sm" style={{ marginTop: 18 }}>
              This row has been acted on; the trace it was decided from is in
              the audit trail.
            </div>
          ) : row.trace ? (
            <>
              <TraceDiagram trace={row.trace} />
              <Residual trace={row.trace} />
            </>
          ) : null}
        </div>

        <div className="exc-detail">
          <div style={{ minWidth: 0 }}>
            {!acted && (
              <>
                <div className="field">
                  <div className="field-label">What happened</div>
                  <div
                    className="prose"
                    style={{ fontSize: 14.5, color: "var(--ink-body)" }}
                  >
                    {row.what}
                  </div>
                </div>

                {/* The three figures, as a list rather than a strip: the
                    difference is the point, and a strip gives all three the
                    same weight. */}
                <div className="field">
                  <div className="field-label">What we know</div>
                  <div className="kv">
                    <span className="k">Bank credit</span>
                    <span>{rupees(row.amount_paise)}</span>
                    {expected && (
                      <>
                        <span className="k">{sentence(expected.label)}</span>
                        <span>{rupees(expected.signed_paise)}</span>
                      </>
                    )}
                    <span className="k strong">
                      {sentence(residualStep?.label ?? "Difference")}
                    </span>
                    <span className={residual > 0 ? "bad" : "strong"}>
                      {rupees(
                        residualStep
                          ? Math.abs(residualStep.signed_paise)
                          : residual,
                      )}
                    </span>
                  </div>
                </div>

                <div className="field">
                  <div className="field-label">Why we stopped</div>
                  <div>
                    <div className="kv">
                      {t && t.candidates.length > 0 && (
                        <>
                          <span className="k">Candidates</span>
                          <span>{t.candidates.length}</span>
                        </>
                      )}
                      {t && t.open_pool_rows > 0 && (
                        <>
                          <span className="k">Open pool</span>
                          <span>
                            {t.open_pool_rows} row
                            {t.open_pool_rows === 1 ? "" : "s"}
                          </span>
                        </>
                      )}
                      {row.confidence !== null && (
                        <>
                          <span className="k">Confidence</span>
                          <span>{row.confidence.toFixed(2)}</span>
                        </>
                      )}
                    </div>
                    <div
                      className={`prose-sm ${why ? "reveal" : "why-clamp"}`}
                      style={{ fontSize: 13.5, marginTop: 9 }}
                    >
                      {row.why}
                    </div>
                    <button
                      className="more"
                      style={{ marginTop: 6 }}
                      onClick={(e) => {
                        e.stopPropagation();
                        setWhy((v) => !v);
                      }}
                      aria-expanded={why}
                    >
                      {why ? "less" : "explain more"}
                    </button>
                  </div>
                </div>

                {/* The recommendation is the ORDER of the buttons, said out
                    loud. The pipeline puts its suggestion first; leaving that
                    implicit made a controller reverse-engineer it. */}
                {row.actions.length > 0 && (
                  <div className="field">
                    <div className="field-label">Recommendation</div>
                    <div
                      className="prose"
                      style={{ fontSize: 14, color: "var(--ink-body)" }}
                    >
                      <strong style={{ fontWeight: 600, color: "var(--ink)" }}>
                        {row.actions[0].label}
                      </strong>{" "}
                      — {row.actions[0].description}
                    </div>
                  </div>
                )}

                <div className="field">
                  <div className="field-label">How this was decided</div>
                  <div>
                    {how ? (
                      <div className="reveal">
                        <div className="layers">
                          {row.layers.map((l) => (
                            <div className="row" key={l.layer}>
                              <span className={`st-${l.state}`}>
                                {l.state === "ok"
                                  ? "✓"
                                  : l.state === "warn"
                                    ? "⚠"
                                    : "—"}
                              </span>
                              <span>{l.layer}</span>
                              <span className="note">— {l.note}</span>
                            </div>
                          ))}
                        </div>

                        {/* The asymmetry is the argument: the model's
                            permissions are short and its prohibitions are
                            long, and that is what makes an LLM safe here. */}
                        <div className="allowed">
                          <div>
                            <div className="head-ok">
                              The model was allowed to
                            </div>
                            <ul>
                              <li>select among candidates</li>
                              <li>decline to select</li>
                              <li>supply a hypothesis</li>
                            </ul>
                          </div>
                          <div>
                            <div className="head-no">
                              The model was not allowed to
                            </div>
                            <ul>
                              <li>compute any amount</li>
                              <li>create a candidate</li>
                              <li>post a journal entry</li>
                              <li>exceed the review ceiling</li>
                            </ul>
                          </div>
                        </div>

                        <div
                          className="prose-sm"
                          style={{ marginTop: 10, fontStyle: "italic" }}
                        >
                          Adjudicated matches cap one notch below the auto-post
                          threshold — an LLM verdict can never move money
                          without a human.
                        </div>
                        <button
                          className="more"
                          style={{ marginTop: 8 }}
                          onClick={(e) => {
                            e.stopPropagation();
                            setHow(false);
                          }}
                        >
                          less
                        </button>
                      </div>
                    ) : (
                      <button
                        className="more"
                        onClick={(e) => {
                          e.stopPropagation();
                          setHow(true);
                        }}
                        aria-expanded={false}
                      >
                        show layer-by-layer
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <Actions
          row={row}
          busy={busy}
          acted={acted}
          onAct={onAct}
          onUndo={onUndo}
        />
      </div>
    </div>
  );
}

/**
 * A collapsed row. Continuous, ruled, and carrying the running balance —
 * this is the ledger, and it stays one.
 */
function ClosedRow({
  row,
  acted,
  onFocus,
}: {
  row: ExceptionCard;
  acted: boolean;
  onFocus: () => void;
}) {
  return (
    <div className={`row ${acted ? "row-settled" : ""}`} onClick={onFocus}>
      <div className={`rail ${acted ? "rail-done" : ""}`} />
      <div className="row-grid">
        <div style={{ minWidth: 0, paddingRight: 32 }}>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 13,
              flexWrap: "wrap",
            }}
          >
            <span className={`row-title ${acted ? "code-done" : ""}`}>
              {row.title}
            </span>
            <span className="row-code">{row.reason_code}</span>
            <span className="row-ref">
              {row.ref}
              {row.value_date ? ` · ${shortDate(row.value_date)}` : ""}
              {row.age_days !== null && (
                <span className="row-age"> · {row.age_days}d</span>
              )}
            </span>
          </div>
          {/* The sentence, not the code, is what the row says. */}
          <div className="plain-lead" style={{ marginTop: 6 }}>
            {row.plain}
          </div>
        </div>

        <div style={{ textAlign: "center" }}>
          <span className="signal-pill">
            {acted
              ? (row.action_state?.action_label ?? "resolved")
              : row.signal}
          </span>
        </div>

        <div className="amount" style={{ fontSize: 17, fontWeight: 500 }}>
          {paise(row.amount_paise)}
        </div>
        <div
          className={`balance ${acted ? "balance-cleared" : "balance-rest"}`}
          style={{ fontSize: 16 }}
        >
          {acted ? "— cleared" : paise(row.open_balance_paise)}
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- actions */

/**
 * The buttons, each one saying what it posts.
 *
 * `posts_preview` comes off the action's own declared shape, checked against
 * what `execute()` actually writes by a test. A preview assembled here would
 * be a second, unverified opinion about the books.
 */
function Actions({
  row,
  busy,
  acted,
  onAct,
  onUndo,
}: {
  row: ExceptionCard;
  busy: boolean;
  acted: boolean;
  onAct: (code: string) => void;
  onUndo: (code: string) => void;
}) {
  if (acted && row.action_state) {
    return (
      <div className="act-row" style={{ display: "block" }}>
        <PostedEntry row={row} />
        <div
          style={{
            display: "flex",
            gap: 18,
            marginTop: 16,
            flexWrap: "wrap",
            alignItems: "flex-start",
          }}
        >
          <div className="act">
            <button
              className="btn"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onUndo(row.action_state!.action_code);
              }}
            >
              Reverse — posts a correcting entry
            </button>
            <div className="act-posts">
              posts a mirror of the entry above
            </div>
          </div>
        </div>
        <div className="prose-sm" style={{ marginTop: 14, maxWidth: "48ch" }}>
          Nothing is deleted.{" "}
          {row.action_state.entry_numbers.join(", ") || "The original"} remains
          in the ledger beside the correcting entry, and the row returns to the
          worklist.
        </div>
      </div>
    );
  }

  return (
    <div className="act-row">
      {row.actions.map((a, i) => (
        <ActionButton
          key={a.code}
          offer={a}
          index={i}
          recommended={a.code === row.recommended_action}
          busy={busy}
          onAct={() => onAct(a.code)}
        />
      ))}
    </div>
  );
}

function ActionButton({
  offer,
  index,
  recommended,
  busy,
  onAct,
}: {
  offer: ActionOffer;
  index: number;
  recommended: boolean;
  busy: boolean;
  onAct: () => void;
}) {
  const none = offer.posts_preview === "no entry posted";
  return (
    <div className="act">
      {recommended ? (
        <div className="act-rec">Recommended</div>
      ) : (
        <div className="act-spacer" />
      )}
      <button
        className={`btn ${recommended ? "btn-primary" : ""} ${busy ? "btn-busy" : ""}`}
        disabled={busy}
        title={offer.description}
        onClick={(e) => {
          e.stopPropagation();
          onAct();
        }}
      >
        {busy && recommended ? (
          <span className="dot blink" style={{ background: "var(--red-ink)" }} />
        ) : (
          index < 3 && <span className="k">{index + 1}</span>
        )}
        {busy && recommended ? "Posting journal entry…" : offer.label}
      </button>
      <div className={`act-posts ${none ? "act-posts-none" : ""}`}>
        {none ? offer.posts_preview : `posts  ${offer.posts_preview}`}
      </div>
    </div>
  );
}

/** The entry that was actually written, once a row has been acted on. */
function PostedEntry({ row }: { row: ExceptionCard }) {
  const state = row.action_state;
  if (!state) return null;
  const offer = row.actions.find((a) => a.code === state.action_code);
  const lines = offer?.posting_lines ?? [];

  return (
    <div className="je-posted">
      <div className="je-posted-head">
        <Tick />
        POSTED ·{" "}
        {state.entry_numbers.length > 0 ? (
          <Gloss term="journal">{state.entry_numbers.join(", ")}</Gloss>
        ) : (
          "no entry"
        )}{" "}
        · {stamp(state.at)}
      </div>
      {lines.map((l, i) => (
        <div
          key={`${l.side}-${l.account}-${i}`}
          className={`je-posted-line ${
            i === lines.length - 1 ? "je-posted-line-last" : ""
          }`}
        >
          <span style={{ paddingLeft: l.side === "Cr" ? 14 : 0 }}>
            {l.account}
          </span>
          <span>{l.side}</span>
          <span className="amt">{rupees(l.amount_paise)}</span>
        </div>
      ))}
      <div className="je-posted-foot">
        <Tick size={12} />
        balanced
      </div>
    </div>
  );
}

/** The verdict, restated in words under the drawing that produced it. */
function Residual({ trace }: { trace: NonNullable<ExceptionCard["trace"]> }) {
  if (trace.residual_paise > 0) {
    return (
      <div className="residual-block">
        <span className="label-sm">Residual unexplained</span>
        <span className="v">{rupees(trace.residual_paise)}</span>
        <span className="note">
          {trace.open_pool_rows > 0
            ? `${trace.open_pool_rows} row${
                trace.open_pool_rows === 1 ? " was" : "s were"
              } open when this ran, netting ${rupees(trace.open_pool_paise)} — no combination closes the gap.`
            : "Nothing was open in the pool that could account for it."}
        </span>
      </div>
    );
  }

  // `residual: 0` with `outcome: "unexplained"` is the most interesting row on
  // the page: the arithmetic ties, several times over, and choosing is the
  // unsolved part. A block keyed off the residual alone would call that
  // "reconciled".
  if (trace.outcome === "unexplained") {
    const n = trace.candidates.length;
    return (
      <div className="residual-block">
        <span className="label-sm">Unresolved</span>
        <span className="v">{n > 1 ? `${n} ways` : "no match"}</span>
        <span className="note">
          {n > 1
            ? "The arithmetic ties every one of these times. The sum is not the problem; choosing between them is."
            : "Nothing was selected for this credit, so it stays on the worklist."}
        </span>
      </div>
    );
  }

  return (
    <div className="residual-block residual-block-ok">
      <span className="label-sm">Reconciled</span>
      <span className="v">{rupees(0)}</span>
      <span className="note">
        Every paisa of this credit is accounted for by the rows above.
      </span>
    </div>
  );
}

/** The pipeline labels its steps mid-sentence ("expected gross", "rounding").
 *  Used as a row label they want a capital, and only the first letter — these
 *  carry account names and codes that must keep their own case. */
function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function Tick({ size = 13 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 13 13" style={{ flex: "none" }}>
      <path
        d="M1.5 7 L4.8 10.3 L11.5 2.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      />
    </svg>
  );
}

/* ----------------------------------------------------------- in transit */

function InTransit({ data }: { data: ExceptionsPayload }) {
  const t = data.in_transit;
  if (t.count === 0) return null;

  return (
    <div className="transit">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 28,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div className="label-sm">In transit</div>
          {/* Teal, not green, and stated as a fact rather than a warning. */}
          <div
            className="lead"
            style={{ marginTop: 10, color: "var(--teal-deep)", maxWidth: "52ch" }}
          >
            This money isn&rsquo;t missing — it just hasn&rsquo;t arrived yet.
          </div>
          <div className="prose-sm" style={{ marginTop: 7, maxWidth: "56ch" }}>
            Card payments reach the bank on <Gloss term="T2">T+2</Gloss>, so
            there is nothing to decide until those two days have passed.
          </div>
        </div>
        <div style={{ textAlign: "right", flex: "none" }}>
          <div className="transit-total" style={{ color: "var(--teal)" }}>
            {rupees(t.total_paise)}
          </div>
          <div
            style={{
              font: "400 11.5px var(--mono)",
              color: "var(--ink-label)",
              marginTop: 4,
            }}
          >
            {t.count} item{t.count === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      {!t.ties && (
        <div className="notice notice-bad">
          The {t.count} line items sum to {rupees(t.items_paise)} but the books
          carry {rupees(t.total_paise)}. The in-transit aggregation is wrong.
        </div>
      )}

      <div
        style={{
          marginTop: 24,
          padding: "16px 20px 18px",
          border: "1px solid var(--teal-edge)",
          background: "var(--teal-panel)",
        }}
      >
        <div className="track-t2" style={{ marginTop: 0 }}>
          <div className="track-t2-ends">
            <span>Captured</span>
            <span>Expected in bank</span>
          </div>
          <div className="track-t2-line">
            {t.items.map((item, i) => (
              <span
                key={item.ref}
                className="track-t2-dot"
                style={{
                  animationDelay: `${(i * 8) / Math.max(t.count, 1)}s`,
                }}
              />
            ))}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 6 }}>
        {t.items.map((item) => (
          <div className="transit-row" key={item.ref} style={{ padding: "17px 0" }}>
            <div className="rail rail-teal" />
            <div className="transit-label">
              <span style={{ color: "var(--ink)" }}>{item.reason_code}</span>
              <span style={{ color: "var(--ink-label)" }}>
                {item.ref}
                {item.value_date
                  ? ` · captured ${shortDate(item.value_date)}`
                  : ""}
              </span>
            </div>
            <div className="amount" style={{ fontSize: 16 }}>
              {paise(item.amount_paise)}
            </div>
            <div />
          </div>
        ))}
      </div>

      <div className="actions" style={{ marginTop: 20 }}>
        {(data.in_transit.items[0]?.actions ?? []).slice(0, 2).map((a, i) => (
          <span
            key={a.code}
            className={`btn ${i === 0 ? "" : "btn-ghost"}`}
            title={a.description}
          >
            {a.label}
          </span>
        ))}
      </div>
    </div>
  );
}
