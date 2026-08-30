"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Gloss } from "@/components/Glossary";
import { api, ApiError, type ReviewItem, type ReviewPayload } from "@/lib/api";
import { paise, pct, rate, rupees, shortDate } from "@/lib/money";
import { useSeed, withSeed } from "@/lib/useSeed";

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
 *
 * **Full width put the queue beside the entry.** It used to be one vertical
 * stack: the open entry, then three of the remaining ten below the fold. The
 * loop was approve → reload → scroll → approve, and the panel moved under you
 * every time. Now the queue is a column on the right, every item in it is
 * clickable, and the entry panel holds still while you work down the list.
 *
 * The confidence gradient stays above both columns because it describes the
 * whole queue, not the open item — it is the band this screen exists in.
 *
 * **P0: the queue says why each entry is in it.** "0.91, please look" asks a
 * reviewer to re-derive the matcher's own reasoning. `why_not_auto` comes off
 * the match — how many orders had to be reconstructed, whether the narration
 * carried a reference, how far short of the ceiling it landed.
 *
 * **P0: rejecting asks where the row belongs.** A rejected entry reappears on
 * /exceptions, and one that arrives saying only "a human declined" has thrown
 * away the most useful thing the human knew. The chooser is a fixed list of
 * reason codes rather than free text, because the exception list is filtered
 * by those codes everywhere else — the server refuses one it does not define.
 */
export default function Page() {
  return (
    <div className="page-wide">
      <Suspense fallback={<div className="notice">loading…</div>}>
        <Review />
      </Suspense>
    </div>
  );
}

function Review() {
  const seed = useSeed();
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);

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

  useEffect(() => {
    setRejecting(false);
  }, [cursor, data]);

  const decide = useCallback(
    async (utr: string, how: "approve" | "reject", code?: string, note?: string) => {
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
          const r = await api.reject(utr, seed, code, note);
          setFlash(
            r.reason_code
              ? `${utr} rejected as ${r.reason_code} — sent to exceptions with your reason attached`
              : `${utr} returned to the exception list`,
          );
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
      // Backspace opens the chooser rather than rejecting outright: a
      // rejection now carries a reason code, and a keystroke that silently
      // picks one for you is worse than one that asks.
      if (ev.key === "Backspace") {
        ev.preventDefault();
        setRejecting(true);
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
      <div style={{ padding: "28px var(--page-pad) 0" }}>
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
            {/* Design 4b: say what the job is before showing the table. The
                queue is an approval, not an investigation, and a controller
                who thinks they have to re-derive the arithmetic will treat
                eleven two-second decisions as eleven two-minute ones. */}
            <div className="lead" style={{ marginTop: 16, maxWidth: "52ch" }}>
              The system worked out what these payments were. You&rsquo;re
              saying yes to the bookkeeping, not redoing the maths.
            </div>
          </div>
          <div className="rv-keys">
            {[
              ["↵", "approve"],
              ["⌫", "reject"],
              ["j k", "next / prev"],
            ].map(([cap, what]) => (
              <div className="keyrow" key={what} style={{ justifyContent: "flex-end" }}>
                {cap.split(" ").map((c) => (
                  <span className="cap" key={c}>
                    {c}
                  </span>
                ))}
                <span>{what}</span>
              </div>
            ))}
          </div>
        </div>

        {/* The band this queue exists in, drawn from the run's own thresholds
            rather than from constants in this file. */}
        <div
          style={{
            marginTop: 24,
            paddingBottom: 22,
            borderBottom: "1px solid var(--gold)",
          }}
        >
          <div className="confbar">
            <div className="track">
              <div
                style={{ width: `${lo * 100}%`, background: "var(--band-exception)" }}
              />
              {/* Gold, not ink: this band is "waiting on a person", and it
                  is the same gold the review segment carries on /exceptions
                  and /books. A reader should not have to learn the colour
                  twice. */}
              <div style={{ width: `${(hi - lo) * 100}%`, background: "var(--gold)" }} />
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
                  color: "var(--gold)",
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
          <div className="prose-md" style={{ marginTop: 18, fontSize: 14 }}>
            Below {lo.toFixed(2)} the system won&rsquo;t guess, and the record
            goes to{" "}
            <Link href={withSeed("/exceptions", seed)} className="linkish">
              exceptions
            </Link>
            . Above {hi.toFixed(2)} it posts without asking. This queue is the
            band in between.
          </div>
        </div>
      </div>

      {flash && <div className="notice">{flash}</div>}
      {error && <div className="notice notice-bad">{error}</div>}

      {current ? (
        <div className="rv-cols">
          <div className="rv-main">
            <Card
              item={current}
              feeRate={data.fee_rate}
              gstRate={data.gst_rate}
              busy={busy}
              seed={seed}
              rejecting={rejecting}
              onApprove={() => decide(current.utr, "approve")}
              onOpenReject={() => setRejecting(true)}
              onCancelReject={() => setRejecting(false)}
              onReject={(code, note) =>
                decide(current.utr, "reject", code, note)
              }
            />
          </div>

          <div className="rv-queue">
            <div className="rv-queue-head">
              <span className="label-sm">Queue</span>
              <span
                style={{
                  font: "400 11px var(--mono)",
                  color: "var(--ink-label)",
                }}
              >
                {items.length} awaiting · {rupees(data.total_paise)}
              </span>
            </div>
            {items.map((i, n) => (
              <button
                key={i.utr}
                type="button"
                className={`rv-item ${n === cursor ? "rv-item-on" : ""}`}
                aria-current={n === cursor ? "true" : undefined}
                onClick={() => setCursor(n)}
              >
                <span className="id">
                  {i.utr}
                  {i.settlement_id ? ` → ${i.settlement_id}` : ""}
                </span>
                <span className="amt">{paise(i.amount_paise)}</span>
                <span className="meta">
                  {i.order_count} order{i.order_count === 1 ? "" : "s"} · value{" "}
                  {shortDate(i.value_date)}
                </span>
                <span className="conf">
                  {i.confidence.toFixed(2)}
                  <span className="meter">
                    <span style={{ width: `${i.confidence * 100}%` }} />
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ padding: "40px var(--page-pad)" }}>
          <div className="prose-xl">
            Nothing awaiting approval. Every prepared entry has been decided.
          </div>
        </div>
      )}
    </div>
  );
}

/** Where a rejected entry can be sent. Fixed, because /exceptions is filtered
 *  by these codes everywhere else and the server refuses one it does not
 *  define — a queue that let a reviewer invent a code would be building an
 *  exception list nobody can filter. */
const REJECT_CODES: { code: string; gloss: string }[] = [
  { code: "AMOUNT_MISMATCH", gloss: "the figures don't add up" },
  { code: "MISSING_IN_LEDGER", gloss: "no record of this sale" },
  { code: "DUPLICATE_UTR", gloss: "this credit is counted twice" },
  { code: "CROSS_PERIOD_REFUND", gloss: "refund against a closed period" },
  { code: "OTHER", gloss: "free text" },
];

function Card({
  item,
  feeRate,
  gstRate,
  busy,
  seed,
  rejecting,
  onApprove,
  onOpenReject,
  onCancelReject,
  onReject,
}: {
  item: ReviewItem;
  feeRate: number | null;
  gstRate: number;
  busy: boolean;
  seed: number | undefined;
  rejecting: boolean;
  onApprove: () => void;
  onOpenReject: () => void;
  onCancelReject: () => void;
  onReject: (code: string, note: string) => void;
}) {
  const entry = item.prepared_entry;
  const refundLine = entry.lines.find((l) => l.account.includes("Refund"));
  const [whyNot, setWhyNot] = useState(false);
  const [code, setCode] = useState(REJECT_CODES[0].code);
  const [note, setNote] = useState("");

  return (
    <div style={{ padding: "22px var(--page-pad) 0", position: "relative" }}>
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

      {item.why_not_auto.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button
            className="more"
            onClick={() => setWhyNot((v) => !v)}
            aria-expanded={whyNot}
          >
            {whyNot ? "hide" : "why not auto-post?"}
          </button>
          {whyNot && (
            <div className="whynot reveal">
              <div className="prose-sm">
                This scored {item.confidence.toFixed(2)} because:
              </div>
              <ul>
                {item.why_not_auto.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Design 4b: the plain line comes FIRST, the table second. The table
          is the evidence; this is the claim it evidences. */}
      <div className="headline" style={{ marginTop: 18, fontSize: 20, maxWidth: "46ch" }}>
        {item.headline}
      </div>

      <div className="je" style={{ marginTop: 24 }}>
        <div className="je-head">
          <span className="label-sm">
            Prepared <Gloss term="journal">journal entry</Gloss>
          </span>
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
            <span>
              {line.account.includes("GST") ? (
                <Gloss term="GST">{line.account}</Gloss>
              ) : (
                line.account
              )}
            </span>
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

      {/* Both outcomes, side by side, BEFORE the buttons. An approve/reject
          pair where only one side says what it does is a pair where one side
          is guessed at. */}
      <div className="consequence">
        <div>
          <div className="head-ok">Approve</div>
          <ul>
            {entry.lines.map((l, i) => (
              <li key={`${l.account}-${i}`}>
                {l.account}{" "}
                <span className="n">
                  {l.debit_paise
                    ? `+${paise(l.debit_paise)}`
                    : `−${paise(l.credit_paise)}`}
                </span>
              </li>
            ))}
            <li className="ok">
              {entry.balanced ? "✓ balanced" : "✕ does not balance"}
            </li>
          </ul>
        </div>
        <div>
          <div className="head-no">Reject</div>
          <div className="prose-sm">
            No entry is posted. {paise(entry.total_debits_paise)} stays
            unresolved and moves to exceptions with your reason code attached.
          </div>
        </div>
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
        <button className="btn" disabled={busy} onClick={onOpenReject}>
          <span className="k">⌫</span> Reject
        </button>
      </div>

      {rejecting && (
        <div className="reject-box reveal">
          <div className="label-sm">Reject — where does it belong?</div>
          <div className="reject-codes">
            {REJECT_CODES.map((c) => (
              <label key={c.code}>
                <input
                  type="radio"
                  name="reject-code"
                  checked={code === c.code}
                  onChange={() => setCode(c.code)}
                />
                <span>
                  {c.code} <em>{c.gloss}</em>
                </span>
              </label>
            ))}
          </div>
          {code === "OTHER" && (
            <input
              className="reject-note"
              placeholder="describe the reason"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
          <div className="actions" style={{ marginTop: 16 }}>
            <button
              className="btn btn-danger"
              disabled={busy || (code === "OTHER" && !note.trim())}
              onClick={() => onReject(code, note)}
              title={
                code === "OTHER" && !note.trim()
                  ? "Give a reason — the row arrives on the worklist carrying it"
                  : "Sends the row to exceptions with this code"
              }
            >
              Send to exceptions
            </button>
            <button className="btn btn-ghost" onClick={onCancelReject}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {/* Design 4b: "reject lands somewhere you can see". A destructive-looking
          button beside an approval needs to say what it actually does, or it
          reads as a delete. */}
      <div className="prose-sm" style={{ marginTop: 12 }}>
        Rejecting doesn&rsquo;t delete anything — the record joins the{" "}
        <Link href={withSeed("/exceptions", seed)} className="linkish">
          unreconciled worklist
        </Link>{" "}
        with your reason attached.
      </div>
    </div>
  );
}
