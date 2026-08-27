"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { TraceDiagram } from "@/components/Trace";
import {
  api,
  ApiError,
  type ExceptionCard,
  type ExceptionsPayload,
} from "@/lib/api";
import { paise, rupees, shortDate, stamp } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * /exceptions — THE HOME SCREEN. Guide §8.1, frames 2a and 3a.
 *
 * Not a tab, not behind a summary, not one panel of a dashboard. The whole
 * inversion §8.1 asks for is that this is what you land on.
 *
 * The third column is a **running open balance**, not a progress bar: row N
 * carries what is still unreconciled from N onwards, descending to `Cleared
 * 0.00` on the last row. The nine amounts genuinely sum to the header figure,
 * and the API says so with `balance_ties` — which this page renders as a
 * visible failure if it is ever false, rather than quietly printing a column
 * that does not add up.
 *
 * In-transit money sits below a rule in its own collection. Never in the list,
 * never in the header count, never blocking a close. Appendix A: it is not a
 * failure, and a screen that shows it as one is factually wrong.
 */
export default function Page() {
  return (
    <Suspense fallback={<div className="notice">loading…</div>}>
      <Exceptions />
    </Suspense>
  );
}

function Exceptions() {
  const seed = useSeed();
  const [data, setData] = useState<ExceptionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);

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

  const rows = (data?.exceptions ?? []).filter(
    (e) => !filter || e.reason_code === filter,
  );

  // j/k to move, 1-3 to act — the triage the header advertises. Bound here
  // rather than per-row so the shortcut list on screen is the whole truth.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.target instanceof HTMLInputElement) return;
      if (ev.key === "j") setCursor((c) => Math.min(c + 1, rows.length - 1));
      if (ev.key === "k") setCursor((c) => Math.max(c - 1, 0));
      const n = Number.parseInt(ev.key, 10);
      if (n >= 1 && n <= 3) {
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

  return (
    <div className="card">
      <div style={{ padding: "30px 40px 0" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 40,
          }}
        >
          <div>
            <div className="eyebrow">Unreconciled · action required</div>
            <div
              style={{
                marginTop: 10,
                display: "flex",
                alignItems: "baseline",
                gap: 14,
              }}
            >
              <div className="hero">{rupees(data.unreconciled_paise)}</div>
              <div className="beside">
                across {data.open} exception{data.open === 1 ? "" : "s"}
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
            <div
              className="prose-lead"
              style={{ marginTop: 12, maxWidth: 520 }}
            >
              Run {stamp(data.started_at)} · {data.auto_posted} posted without
              review · {data.pending_review} awaiting approval. {data.open}{" "}
              remain because no combination of orders, fees and refunds explains
              them.
            </div>
          </div>

          <div style={{ textAlign: "right", flex: "none" }}>
            <div className="label-sm">Triage</div>
            <div className="keys">
              <div className="keyrow">
                <span className="keycap">j</span>
                <span className="keycap">k</span>
                <span>move</span>
              </div>
              <div className="keyrow">
                <span className="keycap">1</span>
                <span style={{ color: "var(--ink-faint)" }}>–</span>
                <span className="keycap">3</span>
                <span>act</span>
              </div>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            gap: 24,
            marginTop: 26,
          }}
        >
          <div className="chips">
            <button
              className="chip"
              aria-pressed={filter === null}
              onClick={() => setFilter(null)}
            >
              ALL {data.open}
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
          <div
            style={{
              flex: "none",
              font: "400 11px var(--mono)",
              color: "var(--ink-muted)",
              whiteSpace: "nowrap",
            }}
          >
            Sorted by amount <span style={{ color: "var(--ink)" }}>↓</span>
          </div>
        </div>
      </div>

      {error && <div className="notice notice-bad">{error}</div>}
      {!data.balance_ties && (
        <div className="notice notice-bad">
          The open-balance column does not tie: {paise(data.residual_paise)}{" "}
          paise left over. This is an aggregation bug, not a display one.
        </div>
      )}

      <div className="ledger-head">
        <div>Exception</div>
        <div style={{ textAlign: "right" }}>Amount</div>
        <div style={{ textAlign: "right" }}>Open balance</div>
      </div>

      <div className="ledger-body">
        {rows.map((row, i) => (
          <Row
            key={row.ref}
            row={row}
            expanded={i === cursor}
            busy={busy === row.ref}
            onFocus={() => setCursor(i)}
            onAct={(code) => act(row.ref, code)}
            onUndo={(code) => undo(row.ref, code)}
          />
        ))}

        <div className="cleared-row">
          <div className="cleared-label">Cleared</div>
          <div />
          <div className="amount balance-cleared">{paise(0)}</div>
        </div>
      </div>

      <InTransit data={data} />
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
  const acted = row.action_state?.state === "acted";
  const open = expanded || acted;

  return (
    <div
      className={`row ${open ? "row-open" : ""} ${acted ? "row-settled" : ""}`}
      onClick={onFocus}
    >
      {open && <div className={`row-bar ${acted ? "row-bar-done" : ""}`} />}

      <div className={`row-grid ${open ? "row-grid-top" : ""}`}>
        <div style={{ paddingRight: 36 }}>
          <div className="row-head" style={{ padding: 0 }}>
            {acted ? (
              <svg width="13" height="13" viewBox="0 0 13 13" style={{ flex: "none" }}>
                <path
                  d="M1.5 7 L4.8 10.3 L11.5 2.6"
                  fill="none"
                  stroke="var(--green)"
                  strokeWidth="1.8"
                />
              </svg>
            ) : (
              <span className="dot" />
            )}
            <span className={`code ${acted ? "code-done" : ""}`}>
              {row.reason_code}
            </span>
            <span className="meta">
              {row.ref}
              {row.value_date ? ` · value ${shortDate(row.value_date)}` : ""}
              {acted && row.action_state
                ? ` · resolved ${stamp(row.action_state.at)} · by ${row.action_state.actor}`
                : ""}
            </span>
          </div>

          {open && (
            <>
              <div className="whatwhy">
                {acted && row.action_state ? (
                  <>
                    <div className="label-sm">Action</div>
                    <div className="prose">{row.action_state.detail}</div>
                  </>
                ) : (
                  <>
                    <div className="label-sm">What</div>
                    <div className="prose">{row.what}</div>
                    <div className="label-sm">Why</div>
                    <div className="prose">
                      {row.why}
                      {row.why_source === "model" && (
                        <span className="hypothesis">
                          {" "}
                          — hypothesis, from the adjudicator.
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>

              {!acted && row.trace && <TraceDiagram trace={row.trace} />}

              <div className="actions">
                {acted && row.action_state ? (
                  <>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() => onUndo(row.action_state!.action_code)}
                    >
                      Reverse — posts a correcting entry
                    </button>
                    {row.action_state.entry_numbers.map((n) => (
                      <span key={n} className="btn btn-ghost">
                        View {n}
                      </span>
                    ))}
                  </>
                ) : (
                  row.actions.map((a, i) => (
                    <button
                      key={a.code}
                      className={`btn ${i === 0 ? "btn-primary" : ""}`}
                      disabled={busy}
                      title={a.description}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        onAct(a.code);
                      }}
                    >
                      {i < 3 && <span className="k">{i + 1}</span>}
                      {a.label}
                      {a.posts_entry ? "" : ""}
                    </button>
                  ))
                )}
              </div>

              {acted && (
                <div
                  className="prose-sm"
                  style={{ marginTop: 11, maxWidth: 520 }}
                >
                  Reversing writes a new entry against{" "}
                  {row.action_state?.entry_numbers.join(", ") || "the original"}{" "}
                  and returns this row to the worklist. Nothing is deleted; both
                  entries stay in the ledger.
                </div>
              )}
            </>
          )}
        </div>

        <div className="amount">{paise(row.amount_paise)}</div>
        <div
          className={`balance ${acted ? "balance-cleared" : ""} ${
            !open && !acted ? "balance-rest" : ""
          }`}
        >
          {acted ? "— cleared" : paise(row.open_balance_paise)}
        </div>
      </div>
    </div>
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
          gap: 24,
        }}
      >
        <div>
          <div className="label-sm">In transit</div>
          <div className="prose-lead" style={{ marginTop: 7, maxWidth: 430 }}>
            Not exceptions. T+2 has not elapsed, so there is nothing to decide.
          </div>
        </div>
        <div style={{ textAlign: "right", flex: "none" }}>
          <div className="transit-total">{rupees(t.total_paise)}</div>
          <div
            style={{
              font: "400 11.5px var(--mono)",
              color: "var(--ink-label)",
              marginTop: 3,
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

      <div style={{ marginTop: 18 }}>
        {t.items.map((item, i) => (
          <div className="transit-row" key={item.ref}>
            <div className="transit-label">
              <svg width="30" height="10" style={{ flex: "none" }}>
                <line
                  x1="0"
                  y1="5"
                  x2="30"
                  y2="5"
                  stroke="var(--green)"
                  strokeWidth="1.8"
                  strokeDasharray="4 5"
                  className="drift"
                  style={{ animationDuration: `${1.2 + i * 0.15}s` }}
                />
              </svg>
              <span style={{ color: "var(--ink)" }}>{item.reason_code}</span>
              <span style={{ color: "var(--ink-label)" }}>
                {item.ref}
                {item.value_date ? ` · captured ${shortDate(item.value_date)}` : ""}
              </span>
            </div>
            <div className="amount">{paise(item.amount_paise)}</div>
            <div />
          </div>
        ))}
      </div>

      {/* Actions come from the registry like everywhere else. Every one of them
          reports posts_entry false — in-transit money moves nothing. */}
      <div className="actions" style={{ marginTop: 16, fontSize: 12 }}>
        {(data.in_transit.items[0]?.actions ?? []).map((a) => (
          <span
            key={a.code}
            className={`btn ${a.posts_entry ? "" : "btn-ghost"}`}
            title={a.description}
            style={{ padding: "8px 14px" }}
          >
            {a.label}
          </span>
        ))}
      </div>
    </div>
  );
}
