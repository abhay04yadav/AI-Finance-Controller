"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { api, ApiError, type ReviewItem, type ReviewPayload } from "@/lib/api";
import { paise, pct, rate, rupees, shortDate } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * /review — approve the entry, not the match. Guide §4.5, §8.4, frame 2b.
 *
 * The prepared journal entry is on the card. That is the whole design: a
 * controller sees the four lines that would post and the total they foot to,
 * and says yes. Showing only "UTR-77291 → 3 orders, confidence 0.91" turns a
 * two-second decision into a two-minute investigation, which is the gate-13
 * stop condition wearing a different hat.
 *
 * **Balance is enforced server-side.** The button below is disabled on an
 * unbalanced entry, but that is a courtesy — `POST /api/review/{utr}/approve`
 * refuses it in the handler, and would refuse it if this page were bypassed
 * entirely. Greying out is a suggestion; the 422 is the guarantee (§9.4).
 */
export default function Page() {
  return (
    <Suspense fallback={<div className="notice">loading…</div>}>
      <Review />
    </Suspense>
  );
}

function Review() {
  const seed = useSeed();
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.review(seed));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [seed]);

  useEffect(() => {
    void load();
  }, [load]);

  const items = data?.items ?? [];
  const current = items[Math.min(cursor, items.length - 1)];

  const decide = useCallback(
    async (utr: string, how: "approve" | "reject") => {
      setBusy(true);
      try {
        if (how === "approve") {
          const r = await api.approve(utr, seed);
          setFlash(
            r.entry_number
              ? `${utr} posted as ${r.entry_number}`
              : `${utr} approved`,
          );
        } else {
          await api.reject(utr, seed);
          setFlash(`${utr} returned to the exception list`);
        }
        setError(null);
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load, seed],
  );

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (!current || busy) return;
      if (ev.key === "Enter") void decide(current.utr, "approve");
      if (ev.key === "Backspace") {
        ev.preventDefault();
        void decide(current.utr, "reject");
      }
      if (ev.key === "j") setCursor((c) => Math.min(c + 1, items.length - 1));
      if (ev.key === "k") setCursor((c) => Math.max(c - 1, 0));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, busy, decide, items.length]);

  if (error && !data) return <div className="notice notice-bad">{error}</div>;
  if (!data) return <div className="notice">loading…</div>;

  const lo = data.review_threshold;
  const hi = data.auto_post_threshold;

  return (
    <div className="card">
      <div style={{ padding: "28px 32px 0" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 28,
          }}
        >
          <div>
            <div className="eyebrow">Awaiting approval</div>
            <div
              style={{
                marginTop: 10,
                display: "flex",
                alignItems: "baseline",
                gap: 12,
              }}
            >
              <div className="hero-sm">{rupees(data.total_paise)}</div>
              <div
                style={{ font: "400 14px/1 var(--mono)", color: "var(--ink-label)" }}
              >
                {data.count} {data.count === 1 ? "entry" : "entries"}
              </div>
            </div>
          </div>
          <div
            style={{
              textAlign: "right",
              flex: "none",
              font: "400 11.5px var(--mono)",
              color: "var(--ink-muted)",
            }}
          >
            <div className="keyrow" style={{ justifyContent: "flex-end" }}>
              <span className="keycap">↵</span>
              <span>approve</span>
            </div>
            <div
              className="keyrow"
              style={{ justifyContent: "flex-end", marginTop: 6 }}
            >
              <span className="keycap">⌫</span>
              <span>reject to exception</span>
            </div>
          </div>
        </div>

        {/* The band this queue exists in, drawn from the run's own thresholds
            rather than from constants in this file. */}
        <div
          style={{
            marginTop: 24,
            paddingBottom: 22,
            borderBottom: "1px solid var(--rule-heavy)",
          }}
        >
          <div className="confbar">
            <div className="track">
              <div
                style={{ width: `${lo * 100}%`, background: "var(--band-exception)" }}
              />
              <div style={{ width: `${(hi - lo) * 100}%`, background: "var(--ink)" }} />
              <div
                style={{ width: `${(1 - hi) * 100}%`, background: "var(--green-edge)" }}
              />
            </div>
            {/* The segments are the REAL thresholds, so the top band is only
                5% of the axis. Labels are anchored to their segment's inner
                edge rather than centred, because centring a 14-character label
                in a 5% column overlaps the one beside it. */}
            <div className="labels">
              <div
                style={{
                  width: `${lo * 100}%`,
                  textAlign: "center",
                  whiteSpace: "nowrap",
                }}
              >
                exception
              </div>
              <div
                style={{
                  width: `${(hi - lo) * 100}%`,
                  textAlign: "center",
                  color: "var(--ink)",
                  whiteSpace: "nowrap",
                }}
              >
                review
              </div>
              <div
                style={{
                  width: `${(1 - hi) * 100}%`,
                  textAlign: "right",
                  whiteSpace: "nowrap",
                }}
              >
                posts silently
              </div>
            </div>
            <div className="edges">
              <div style={{ width: `${lo * 100}%` }} />
              <div
                style={{
                  width: `${(hi - lo) * 100}%`,
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span>{lo.toFixed(2)}</span>
                <span>{hi.toFixed(2)}</span>
              </div>
              <div style={{ width: `${(1 - hi) * 100}%` }} />
            </div>
          </div>
          <div className="prose-md" style={{ marginTop: 14 }}>
            Everything below {hi.toFixed(2)} is prepared but not posted. The
            money sits in suspense until someone here says yes, which is what
            keeps the books from claiming what nobody confirmed.
          </div>
        </div>
      </div>

      {flash && <div className="notice">{flash}</div>}
      {error && <div className="notice notice-bad">{error}</div>}

      {current ? (
        <Card
          item={current}
          feeRate={data.fee_rate}
          gstRate={data.gst_rate}
          busy={busy}
          onApprove={() => decide(current.utr, "approve")}
          onReject={() => decide(current.utr, "reject")}
        />
      ) : (
        <div style={{ padding: "40px 32px" }}>
          <div className="prose-xl">
            Nothing awaiting approval. Every prepared entry has been decided.
          </div>
        </div>
      )}

      {items.length > 1 && (
        <div style={{ margin: "24px 0 0", padding: "0 32px 30px" }}>
          <div style={{ borderTop: "1px solid var(--rule)" }}>
            {items.slice(1, 4).map((i) => (
              <div
                key={i.utr}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 130px 74px",
                  padding: "13px 0",
                  borderBottom: "1px solid var(--rule-light)",
                  alignItems: "center",
                  font: "400 12.5px var(--mono)",
                }}
              >
                <span style={{ color: "var(--ink-row)" }}>
                  {i.utr}
                  {i.settlement_id ? ` → ${i.settlement_id}` : ""}{" "}
                  <span style={{ color: "var(--ink-label)" }}>
                    · {i.order_count} order{i.order_count === 1 ? "" : "s"}
                  </span>
                </span>
                <span className="num" style={{ fontSize: 14 }}>
                  {paise(i.amount_paise)}
                </span>
                <span className="num" style={{ color: "var(--ink-muted)" }}>
                  {i.confidence.toFixed(2)}
                </span>
              </div>
            ))}
            {items.length > 4 && (
              <div
                style={{
                  padding: "13px 0",
                  font: "400 12px var(--mono)",
                  color: "var(--ink-label)",
                }}
              >
                + {items.length - 4} more · same shape
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Card({
  item,
  feeRate,
  gstRate,
  busy,
  onApprove,
  onReject,
}: {
  item: ReviewItem;
  feeRate: number | null;
  gstRate: number;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const entry = item.prepared_entry;
  const refundLine = entry.lines.find((l) => l.account.includes("Refund"));

  return (
    <div style={{ padding: "22px 32px 0", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 22,
          bottom: 0,
          width: 3,
          background: "var(--ink)",
        }}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 20,
        }}
      >
        <div style={{ font: "500 13.5px var(--mono)", color: "var(--ink)" }}>
          {item.utr}
          {item.settlement_id ? ` → ${item.settlement_id}` : ""}
          <span style={{ fontWeight: 400, color: "var(--ink-label)" }}>
            {" "}
            · {item.order_count} order{item.order_count === 1 ? "" : "s"} · value{" "}
            {shortDate(item.value_date)}
          </span>
        </div>
        <div
          style={{ display: "flex", alignItems: "center", gap: 9, flex: "none" }}
        >
          <span
            style={{
              font: "400 11px var(--mono)",
              color: "var(--ink-label)",
              letterSpacing: ".08em",
            }}
          >
            CONF
          </span>
          <span style={{ font: "500 14px var(--mono)", color: "var(--ink)" }}>
            {item.confidence.toFixed(2)}
          </span>
          <span className="meter">
            <span style={{ width: `${item.confidence * 100}%` }} />
          </span>
        </div>
      </div>

      <div className="je">
        <div className="je-head">
          <span className="label-sm">Prepared journal entry</span>
          <span
            style={{ font: "400 11px var(--mono)", color: "var(--ink-label)" }}
          >
            fee model {rate(feeRate)} · GST {pct(gstRate, 0)} on fee
          </span>
        </div>
        <div className="je-row je-row-head" style={{ display: "grid" }}>
          <span>Account</span>
          <span className="num">Debit</span>
          <span className="num">Credit</span>
        </div>
        {entry.lines.map((line, i) => (
          <div
            key={`${line.account}-${i}`}
            className={`je-row ${i === entry.lines.length - 1 ? "je-row-last" : ""}`}
          >
            <span>{line.account}</span>
            <span className="num">
              {line.debit_paise ? paise(line.debit_paise) : ""}
            </span>
            <span className="num">
              {line.credit_paise ? paise(line.credit_paise) : ""}
            </span>
          </div>
        ))}
        <div className="je-foot">
          <span className={`balanced ${entry.balanced ? "" : "unbalanced"}`}>
            {entry.balanced ? (
              <svg width="13" height="13" viewBox="0 0 13 13" style={{ flex: "none" }}>
                <path
                  d="M1.5 7 L4.8 10.3 L11.5 2.6"
                  fill="none"
                  stroke="var(--green)"
                  strokeWidth="1.8"
                />
              </svg>
            ) : (
              <span>✕</span>
            )}
            {entry.balanced ? "BALANCED" : "DOES NOT BALANCE"}
          </span>
          <span className="num">{paise(entry.total_debits_paise)}</span>
          <span className="num">{paise(entry.total_credits_paise)}</span>
        </div>
      </div>

      <div className="prose-sm" style={{ marginTop: 10 }}>
        {refundLine
          ? "The refund takes its own debit line rather than being netted into the receivable — a controller should see what was sold and what came back, not one smaller number."
          : "No refund against this settlement. When one exists it takes its own debit line; GST is never folded into the fee."}
      </div>

      <div className="prose-sm" style={{ marginTop: 8, color: "var(--ink-muted)" }}>
        {item.reason}
      </div>

      <div className="actions" style={{ marginTop: 18 }}>
        <button
          className="btn btn-primary"
          disabled={busy || !entry.balanced}
          onClick={onApprove}
          title={
            entry.balanced
              ? "Posts the entry and clears the suspense holding"
              : "Refused: debits do not equal credits"
          }
        >
          <span className="k">↵</span> Approve &amp; post
        </button>
        <button className="btn" disabled={busy} onClick={onReject}>
          <span className="k">⌫</span> Reject → exception
        </button>
      </div>
    </div>
  );
}
