"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { api, ApiError, type BooksPayload } from "@/lib/api";
import { paise, rupees, shortDate, stamp } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * /books — the tie-out, shown as arithmetic. Guide §1.6, §4.5, frames 2c & 3b.
 *
 * Four addends and a total, with a claim that they add up. The claim is not
 * decoration: `tie_out.ties` is computed on the server from the same addends
 * shown here, and if it is ever false this page prints the discrepancy instead
 * of the tick. A green check drawn unconditionally is worth nothing.
 *
 * The cleared state (frame 3b) is the same screen with the open balance at
 * zero, and "resolved by hand N, of those reversed M" comes from the audit
 * trail — a projection over recorded events, not a counter this component keeps.
 */
export default function Page() {
  return (
    <Suspense fallback={<div className="notice">loading…</div>}>
      <Books />
    </Suspense>
  );
}

function Books() {
  const seed = useSeed();
  const [data, setData] = useState<BooksPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.books(seed));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [seed]);

  useEffect(() => {
    void load();
  }, [load]);

  async function close() {
    setBusy(true);
    try {
      const r = await api.close(seed);
      setFlash(`Period closed ${stamp(r.closed_at)}`);
      setError(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function exportTrail() {
    setBusy(true);
    try {
      const trail = await api.auditTrail(seed);
      // A file the controller keeps, not a table they screenshot. The download
      // is built from the response, so what lands on disk is exactly what the
      // server recorded.
      const blob = new Blob([JSON.stringify(trail, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-trail-${data?.run_id ?? "run"}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setFlash(`Exported ${trail.events.length} audit event(s)`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <div className="notice notice-bad">{error}</div>;
  if (!data) return <div className="notice">loading…</div>;

  const d = data.disposition;
  const closed = data.how_it_closed;
  const nothingOpen = d.exceptions.count === 0;

  return (
    <div className="card">
      <div
        style={{
          padding: "28px 32px 22px",
          borderBottom: "1px solid var(--rule-heavy)",
        }}
      >
        <div className="eyebrow">
          Books · {data.label}
          {data.closed ? " · closed" : ""}
        </div>
        {nothingOpen ? (
          <>
            <div
              style={{
                marginTop: 12,
                display: "flex",
                alignItems: "baseline",
                gap: 16,
              }}
            >
              <div className="hero-lg">{rupees(0)}</div>
              <div className="beside">nothing open</div>
            </div>
            <div className="prose-xl" style={{ marginTop: 16, maxWidth: 470 }}>
              Every credit in this run ties to a trace. The books close without a
              judgement call outstanding.
            </div>
          </>
        ) : (
          <div className="prose-xl" style={{ marginTop: 10, maxWidth: 520 }}>
            Every rupee that moved is on one of three lines, and the three lines
            add up.
          </div>
        )}
      </div>

      {flash && <div className="notice">{flash}</div>}
      {error && <div className="notice notice-bad">{error}</div>}

      {/* ------------------------------------------------- disposition */}
      <div style={{ padding: "24px 32px 0" }}>
        <div className="label-sm">Disposition</div>
        <div className="tbl" style={{ marginTop: 12 }}>
          <Disposition
            colour="var(--green)"
            label="Auto-posted"
            count={d.auto_posted.count}
            paise={d.auto_posted.paise}
          />
          <Disposition
            colour="var(--ink)"
            label="Pending review"
            count={d.pending_review.count}
            paise={d.pending_review.paise}
          />
          <Disposition
            colour="var(--red)"
            label="Exceptions"
            count={d.exceptions.count}
            paise={d.exceptions.paise}
            unit="items"
          />
        </div>
      </div>

      {/* ------------------------------------------------------ tie-out */}
      <div style={{ padding: "26px 32px 0" }}>
        <div className="label-sm">Tie-out</div>
        <div
          className="tbl"
          style={{ marginTop: 12, font: "400 14px var(--mono)" }}
        >
          {data.tie_out.addends.map((line) => (
            <div
              key={line.label}
              style={{
                display: "grid",
                gridTemplateColumns: "24px 1fr 200px",
                padding: "9px 0",
                alignItems: "baseline",
                color: line.paise === 0 ? "var(--ink-label)" : undefined,
              }}
            >
              <span style={{ color: "var(--ink-label)" }}>{line.sign}</span>
              <span>{line.label}</span>
              <span className="num" style={{ fontSize: 17 }}>
                {paise(line.paise)}
              </span>
            </div>
          ))}
          <div style={{ display: "grid", gridTemplateColumns: "24px 1fr 200px" }}>
            <span />
            <span />
            <span style={{ borderTop: "1px solid var(--ink)" }} />
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "24px 1fr 200px",
              padding: "11px 0 0",
              alignItems: "baseline",
            }}
          >
            <span style={{ color: "var(--ink-label)" }}>=</span>
            <span style={{ fontWeight: 500 }}>{data.tie_out.total.label}</span>
            <span className="num" style={{ fontSize: 19, fontWeight: 500 }}>
              {paise(data.tie_out.total.paise)}
            </span>
          </div>

          {data.tie_out.ties ? (
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                alignItems: "center",
                gap: 9,
                marginTop: 10,
                font: "400 11.5px var(--mono)",
                letterSpacing: ".06em",
                color: "var(--green)",
              }}
            >
              <svg width="13" height="13" viewBox="0 0 13 13">
                <path
                  d="M1.5 7 L4.8 10.3 L11.5 2.6"
                  fill="none"
                  stroke="var(--green)"
                  strokeWidth="1.8"
                />
              </svg>
              TIES TO THE RUPEE
            </div>
          ) : (
            <div className="notice notice-bad">
              The addends come to {rupees(data.tie_out.computed_paise)} against a
              stated {rupees(data.tie_out.total.paise)} — off by{" "}
              {rupees(data.tie_out.delta_paise)}.
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------- how it closed */}
      <div style={{ padding: "26px 32px 0" }}>
        <div className="label-sm">How it closed</div>
        <div className="tbl" style={{ marginTop: 12 }}>
          <Closed label="Auto-posted, no review" row={closed.auto_posted} />
          <Closed label="Approved in review" row={closed.approved_in_review} />
          <Closed label="Resolved by hand" row={closed.resolved_by_hand} />
          <Closed
            label="of those, reversed"
            row={closed.of_those_reversed}
            muted
          />
        </div>
        <div className="prose-sm" style={{ marginTop: 10 }}>
          A reversal is a posting, not an erasure — the correcting entry sits
          beside the original in the ledger, and both keep their numbers.
        </div>
      </div>

      {/* --------------------------------------------------- in transit */}
      <div className="panel-green" style={{ margin: "26px 32px 0" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 24,
          }}
        >
          <div>
            <div className="label-sm">Cash in transit</div>
            <div className="prose-md" style={{ marginTop: 8, maxWidth: 330 }}>
              Left the customer, not yet landed. {data.in_transit.count}{" "}
              settlements with a T+2 journey still ahead of them. They do not
              hold the close open.
            </div>
          </div>
          <div style={{ textAlign: "right", flex: "none" }}>
            <div
              style={{
                font: "500 26px/1 var(--mono)",
                letterSpacing: "-.02em",
                color: "var(--green)",
              }}
            >
              {rupees(data.in_transit.total_paise)}
            </div>
            <div
              style={{
                font: "400 11px var(--mono)",
                color: "var(--green-label)",
                marginTop: 4,
              }}
            >
              {data.in_transit.count} settlement
              {data.in_transit.count === 1 ? "" : "s"}
            </div>
          </div>
        </div>

        {!data.in_transit.ties && (
          <div className="notice notice-bad">
            The line items below do not sum to the total above. The in-transit
            aggregation is wrong.
          </div>
        )}

        <TransitTrack items={data.in_transit.items} />
      </div>

      {/* ------------------------------------------------------ suspense */}
      <div style={{ padding: "24px 32px 0" }}>
        <div className="label-sm">Suspense</div>
        <div className="tbl" style={{ marginTop: 12 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 200px",
              padding: "10px 0",
              borderBottom: "1px solid var(--rule-light)",
              alignItems: "baseline",
            }}
          >
            <span>In suspense</span>
            <span className="num" style={{ fontSize: 17 }}>
              {paise(data.suspense.paise)}
            </span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 200px",
              padding: "10px 0 0",
              alignItems: "baseline",
              color: "var(--ink-muted)",
            }}
          >
            <span style={{ paddingLeft: 22, fontSize: 12.5 }}>
              of which awaiting your approval
            </span>
            <span className="num" style={{ fontSize: 15 }}>
              {paise(data.suspense.awaiting_approval_paise)}
            </span>
          </div>
        </div>
      </div>

      <div style={{ padding: "24px 32px 32px" }}>
        <div className="actions" style={{ marginTop: 0 }}>
          <button
            className="btn btn-primary"
            disabled={busy || data.closed || !nothingOpen}
            onClick={close}
            title={
              nothingOpen
                ? "Close the period"
                : `${d.exceptions.count} exception(s) still need a decision`
            }
          >
            {data.closed ? "Closed" : `Close ${data.label}`}
          </button>
          <button className="btn" disabled={busy} onClick={exportTrail}>
            Export audit trail
          </button>
        </div>
        {!nothingOpen && (
          <div className="prose-sm" style={{ marginTop: 11 }}>
            The close is held by {d.exceptions.count} unresolved exception
            {d.exceptions.count === 1 ? "" : "s"}, not by the{" "}
            {data.in_transit.count} settlements in transit. Money on its way is
            not money missing.
          </div>
        )}
      </div>
    </div>
  );
}

function Disposition({
  colour,
  label,
  count,
  paise: amount,
  unit = "entries",
}: {
  colour: string;
  label: string;
  count: number;
  paise: number;
  unit?: string;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 108px 168px",
        padding: "11px 0",
        borderBottom: "1px solid var(--rule-light)",
        alignItems: "baseline",
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <span
          style={{
            width: 7,
            height: 7,
            background: colour,
            borderRadius: "50%",
          }}
        />
        {label}
      </span>
      <span className="num dim">
        {count} {unit}
      </span>
      <span className="num" style={{ fontSize: 16 }}>
        {paise(amount)}
      </span>
    </div>
  );
}

function Closed({
  label,
  row,
  muted,
}: {
  label: string;
  row: { count: number; paise: number } | undefined;
  muted?: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 100px 168px",
        padding: "11px 0",
        borderBottom: "1px solid var(--rule-light)",
        alignItems: "baseline",
        color: muted ? "var(--ink-muted)" : undefined,
      }}
    >
      <span>{label}</span>
      <span className="num dim">{row?.count ?? 0}</span>
      <span className="num" style={{ fontSize: 15 }}>
        {paise(row?.paise ?? 0)}
      </span>
    </div>
  );
}

/** The T+2 journey, one dot per settlement, spaced by amount order. */
function TransitTrack({
  items,
}: {
  items: { ref: string; amount_paise: number; value_date: string | null }[];
}) {
  if (items.length === 0) return null;
  const step = 600 / (items.length + 1);
  return (
    <svg
      viewBox="0 0 600 58"
      width="100%"
      style={{ display: "block", marginTop: 16, overflow: "visible" }}
    >
      <g fontFamily="var(--mono)" fontSize="10" fill="var(--green-label)">
        <text x="0" y="12">
          CAPTURED
        </text>
        <text x="600" y="12" textAnchor="end">
          EXPECTED IN BANK
        </text>
      </g>
      <line x1="0" y1="26" x2="600" y2="26" stroke="var(--green-rule)" strokeWidth="1" />
      <line
        x1="0"
        y1="26"
        x2="600"
        y2="26"
        stroke="var(--green)"
        strokeWidth="2"
        strokeDasharray="5 7"
        className="drift"
      />
      {items.map((item, i) => {
        const x = step * (i + 1);
        return (
          <g key={item.ref}>
            <circle cx={x} cy="26" r="3.5" fill="var(--green)" />
            <text
              x={x}
              y="46"
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="10.5"
              fill="var(--green)"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {paise(item.amount_paise)}
            </text>
            <text
              x={x}
              y="58"
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="9.5"
              fill="var(--green-faint)"
            >
              {shortDate(item.value_date)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
