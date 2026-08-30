"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AuditTrail } from "@/components/AuditTrail";
import { Bar, Legend } from "@/components/Bar";
import { Gloss } from "@/components/Glossary";
import { api, ApiError, type BooksPayload } from "@/lib/api";
import { paise, pct, rupees, shortDate, stamp } from "@/lib/money";
import { useSeed, withSeed } from "@/lib/useSeed";

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
 *
 * **Full width, but still one column.** This screen is a report, not a
 * worklist: disposition, then tie-out, then cash in transit, then suspense,
 * read once from the top. Splitting a narrative into two columns would ask
 * the reader to choose an order, and the order is the argument. So the width
 * goes into the LINES instead — labels hard left, figures hard right where
 * they align, and the hairline under each row carries the eye across the gap.
 * Cash in transit becomes its own band edge to edge, because four settlements
 * spreading along a T+2 timeline is the one thing here that wanted room.
 */
export default function Page() {
  return (
    <div className="page-wide">
      <Suspense fallback={<div className="notice">loading…</div>}>
        <Books />
      </Suspense>
    </div>
  );
}

function Books() {
  const seed = useSeed();
  const [data, setData] = useState<BooksPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [audit, setAudit] = useState(false);

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
  // Every rupee this run touched. Summed from the three dispositions rather
  // than taken from a fourth field, so the bar and the rows below it are
  // guaranteed to be about the same money.
  const dispositionTotal =
    d.auto_posted.paise + d.pending_review.paise + d.exceptions.paise;

  return (
    <div className="card">
      {audit && <AuditTrail onClose={() => setAudit(false)} />}

      {/* A closed period is read-only, and the screen should say so before a
          controller reaches for a button that will refuse them. */}
      {data.closed && (
        <div className="closed-banner">
          <span className="k">
            <Tick /> Period closed · read-only
          </span>
          <span className="v">
            closed {stamp(data.closed_at)} · {data.run_id}
          </span>
        </div>
      )}

      <div
        style={{
          padding: "28px var(--page-pad) 22px",
          borderBottom: "1px solid var(--green)",
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
      <div style={{ padding: "30px var(--page-pad) 0" }}>
        {/* The same funnel /exceptions opens on, seen from the other end.
            There it is "what is left"; here it is "what closed". Two screens
            drawing one shape is what makes them feel like one system. */}
        <div className="funnel-echo">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              gap: 20,
              flexWrap: "wrap",
            }}
          >
            <span className="label-sm">Same funnel, from the other end</span>
            <span
              style={{
                font: "400 10.5px var(--mono)",
                color: "var(--ink-faint)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {d.auto_posted.count + d.pending_review.count + d.exceptions.count}{" "}
              in · {d.auto_posted.count} posted · {d.pending_review.count}{" "}
              in review · {d.exceptions.count} open
            </span>
          </div>
          <Bar
            height={10}
            segments={[
              {
                share: share(d.auto_posted.paise, dispositionTotal),
                color: "var(--green)",
                label: "posted",
              },
              {
                share: share(d.pending_review.paise, dispositionTotal),
                color: "var(--gold)",
                label: "review",
              },
              {
                share: share(d.exceptions.paise, dispositionTotal),
                color: "var(--red)",
                label: "open",
              },
            ]}
          />
        </div>

        <div className="label-sm">Disposition</div>
        <div className="prose-md" style={{ marginTop: 8, maxWidth: 480 }}>
          Where every payment from this run ended up. Each line opens the
          screen that holds it.
        </div>

        {/* Design 4c: the shape first. Three figures in a column take a
            moment to compare; three arcs do not. A ring rather than a second
            bar, because the echo above is already a bar of these same three
            numbers and two identical charts 80px apart read as a mistake.
            Shares come from the same figures rendered below, so the picture
            and the rows cannot disagree. */}
        <div className="disp">
          <Donut
            slices={[
              {
                share: share(d.auto_posted.paise, dispositionTotal),
                color: "var(--green)",
              },
              {
                share: share(d.pending_review.paise, dispositionTotal),
                color: "var(--gold)",
              },
              {
                share: share(d.exceptions.paise, dispositionTotal),
                color: "var(--red)",
              },
            ]}
            total={rupees(dispositionTotal)}
          />
          <div className="disp-rows">
          <Disposition
            colour="var(--green)"
            label={<Gloss term="autores">Auto-posted</Gloss>}
            count={d.auto_posted.count}
            paise={d.auto_posted.paise}
            share={share(d.auto_posted.paise, dispositionTotal)}
          />
          <Disposition
            colour="var(--gold)"
            label="Pending review"
            count={d.pending_review.count}
            paise={d.pending_review.paise}
            share={share(d.pending_review.paise, dispositionTotal)}
            href={withSeed("/review", seed)}
          />
          <Disposition
            colour="var(--red)"
            label="Exceptions"
            count={d.exceptions.count}
            paise={d.exceptions.paise}
            share={share(d.exceptions.paise, dispositionTotal)}
            unit="items"
            href={withSeed("/exceptions", seed)}
          />
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------ tie-out */}
      <div style={{ padding: "32px var(--page-pad) 0" }}>
        <div className="label-sm">Tie-out</div>
        <div className="prose-md" style={{ marginTop: 8, maxWidth: 480 }}>
          The cash that landed, plus what the gateway kept, equals the sales we
          recorded. If those don&rsquo;t match, the close is wrong.
        </div>
        <div
          className="tbl"
          style={{ marginTop: 18, font: "400 14px var(--mono)" }}
        >
          {data.tie_out.addends.map((line) => (
            <div
              className="rpt rpt-tie"
              key={line.label}
              style={{
                color: line.paise === 0 ? "var(--ink-label)" : undefined,
              }}
            >
              <span style={{ color: "var(--ink-label)" }}>{line.sign}</span>
              <span>{line.label}</span>
              <span className="fig">{paise(line.paise)}</span>
            </div>
          ))}
          <div className="rpt rpt-tie" style={{ padding: 0 }}>
            <span />
            <span />
            <span style={{ borderTop: "1px solid var(--ink)" }} />
          </div>
          <div className="rpt rpt-tie rpt-total" style={{ padding: "11px 0 0" }}>
            <span style={{ color: "var(--ink-label)" }}>=</span>
            <span style={{ fontWeight: 500 }}>{data.tie_out.total.label}</span>
            <span className="fig">{paise(data.tie_out.total.paise)}</span>
          </div>

          {/* Design 4c: the same total as a bar, with the last 2% enlarged.
              Cash is 97.8% of revenue, so on a full-width bar the fee and the
              GST are two hairlines — present, unreadable, and exactly the part
              a controller is here to check. The inset is a magnifier, and the
              bracket says so. */}
          <TieOutBar
            cash={data.tie_out.addends[0]?.paise ?? 0}
            fee={data.tie_out.addends[1]?.paise ?? 0}
            gst={data.tie_out.addends[2]?.paise ?? 0}
            total={data.tie_out.total.paise}
          />

          {data.tie_out.ties ? (
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                alignItems: "center",
                gap: 9,
                marginTop: 16,
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
      <div style={{ padding: "26px var(--page-pad) 0" }}>
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
      <div className="panel-green bk-band">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 24,
          }}
        >
          <div>
            <div className="label-sm" style={{ color: "var(--teal-label)" }}>
              Cash in transit
            </div>
            <div
              className="lead"
              style={{
                marginTop: 10,
                fontSize: 16,
                color: "var(--teal-deep)",
                maxWidth: "48ch",
              }}
            >
              Left the customer, not yet landed. {data.in_transit.count}{" "}
              <Gloss term="settlement" teal>
                settlements
              </Gloss>{" "}
              with a <Gloss term="T2" teal>T+2</Gloss> journey still ahead of
              them.
            </div>
          </div>
          <div style={{ textAlign: "right", flex: "none" }}>
            <div
              style={{
                font: "500 26px/1 var(--mono)",
                letterSpacing: "-.02em",
                color: "var(--teal)",
              }}
            >
              {rupees(data.in_transit.total_paise)}
            </div>
            <div
              style={{
                font: "400 11px var(--mono)",
                color: "var(--teal-label)",
                marginTop: 5,
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
      <div style={{ padding: "24px var(--page-pad) 0" }}>
        <div className="label-sm">
          <Gloss term="suspense">Suspense</Gloss>
        </div>
        <div className="prose-md" style={{ marginTop: 8, maxWidth: 480 }}>
          Money parked in a named account because we can&rsquo;t yet say what it
          was for. Visible, not written off.
        </div>
        <div className="tbl" style={{ marginTop: 12 }}>
          <div className="rpt rpt-susp">
            <span>In suspense</span>
            <span className="fig">{paise(data.suspense.paise)}</span>
          </div>
          <div
            className="rpt rpt-susp"
            style={{ borderBottom: 0, color: "var(--ink-muted)" }}
          >
            <Link
              href={withSeed("/review", seed)}
              className="linkish"
              style={{ paddingLeft: 22, fontSize: 12.5, justifySelf: "start" }}
            >
              of which awaiting your approval
            </Link>
            <span className="fig" style={{ fontSize: 16 }}>
              {paise(data.suspense.awaiting_approval_paise)}
            </span>
          </div>
        </div>
      </div>

      <div style={{ padding: "24px var(--page-pad) 32px" }}>
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
          <button className="btn" onClick={() => setAudit(true)}>
            View audit trail
          </button>
          <button className="btn btn-ghost" disabled={busy} onClick={exportTrail}>
            Export .json
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

/**
 * One disposition line, and — design 4c — a link to the screen that holds it.
 *
 * "Each disposition row goes to the screen it summarises." A summary that
 * names a number and gives you no way to reach the records behind it is a
 * dead end, and this page is where a controller decides what to open next.
 */
function Disposition({
  colour,
  label,
  count,
  paise: amount,
  share: portion,
  unit = "entries",
  href,
}: {
  colour: string;
  label: React.ReactNode;
  count: number;
  paise: number;
  share: number;
  unit?: string;
  href?: string;
}) {
  const style = {
    textDecoration: "none",
    color: "var(--ink)",
  } as const;

  const body = (
    <>
      <span style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <span
          style={{
            width: 7,
            height: 7,
            background: colour,
            borderRadius: "50%",
            flex: "none",
          }}
        />
        <span style={href ? { borderBottom: "1px solid var(--edge)" } : undefined}>
          {label}
        </span>
      </span>
      {/* The share in the row's own colour: three figures in a column take a
          moment to rank, three percentages in three colours do not. */}
      <span className="num" style={{ color: colour, fontSize: 12.5 }}>
        {pct(portion)}
      </span>
      <span className="num dim">
        {count} {unit}
      </span>
      <span className="fig">{paise(amount)}</span>
    </>
  );

  return href ? (
    <Link href={href} className="rpt rpt-disp" style={style}>
      {body}
    </Link>
  ) : (
    <div className="rpt rpt-disp" style={style}>
      {body}
    </div>
  );
}

/**
 * The tie-out as a proportion, plus a magnified view of its tail.
 *
 * Two bars, and the second is the point. Cash in bank is ~97.8% of revenue
 * recognised; drawn to scale, the gateway fee and the GST credit are a pixel
 * each. Those two lines are the merchant's actual cost of taking card
 * payments and the tax they can reclaim — the figures most worth looking at,
 * and the ones a faithful bar hides. So the tail is drawn again at its own
 * scale, under a bracket that says which part has been enlarged.
 */
function TieOutBar({
  cash,
  fee,
  gst,
  total,
}: {
  cash: number;
  fee: number;
  gst: number;
  total: number;
}) {
  if (total <= 0) return null;
  const tail = fee + gst;
  return (
    <div
      style={{
        marginTop: 20,
        paddingTop: 18,
        borderTop: "1px solid var(--rule-light)",
      }}
    >
      <Bar
        height={15}
        segments={[
          { share: share(cash, total), color: "var(--slate)", label: "cash" },
          { share: share(fee, total), color: "var(--ink)", label: "fee" },
          { share: share(gst, total), color: "var(--ink-label)", label: "GST" },
        ]}
      />
      {tail > 0 && (
        <div style={{ marginTop: 2 }}>
          {/* A funnel from the slice to the enlarged bar, not a bracket with
              a tick under it. Cash is ~98% of revenue, so the fee and the GST
              are two hairlines on a faithful bar — and those two lines are
              the merchant's actual cost of taking cards and the tax they can
              reclaim, which is the part worth looking at. The figure has to
              say WHICH sliver was blown up, or the enlarged bar is just a
              second chart of unstated provenance. */}
          <svg
            viewBox="0 0 100 26"
            preserveAspectRatio="none"
            width="100%"
            height="26"
            aria-hidden
            style={{ display: "block" }}
          >
            {/* Fill first, edges over it — drawn the other way round the
                stroke disappears under the polygon. The fill is --paper-sunk
                rather than --track, which is within two points of the page
                ground and rendered the whole figure invisible. */}
            <path
              d={`M${(1 - share(tail, total)) * 100} 0 H100 V26 H0 Z`}
              fill="var(--paper-sunk)"
              stroke="none"
            />
            <path
              d={`M${(1 - share(tail, total)) * 100} 0 L0 26`}
              fill="none"
              stroke="var(--rule-key)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
            <path
              d="M100 0 V26"
              fill="none"
              stroke="var(--rule-key)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          {/* Nothing between the funnel and the bar it points at — the
              caption used to sit here and the connection read as two
              unrelated figures. It joins the legend underneath instead. */}
          <div>
            <Bar
              height={11}
              segments={[
                { share: share(fee, tail), color: "var(--ink)", label: "fee" },
                {
                  share: share(gst, tail),
                  color: "var(--ink-label)",
                  label: "GST",
                },
              ]}
            />
            <Legend
              items={[
                { label: `fee ${paise(fee)}`, color: "var(--ink)" },
                { label: `GST ${paise(gst)}`, color: "var(--ink-label)" },
              ]}
              trailing={`the last ${pct(share(tail, total), 2)} of revenue, enlarged`}
            />
          </div>
        </div>
      )}
    </div>
  );
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

/**
 * Three shares as one ring, with the total in the middle.
 *
 * Drawn with stroke-dasharray on concentric circles rather than arc paths:
 * one number per slice, no trigonometry, and a slice of zero renders as
 * nothing rather than as a hairline artefact at the twelve o'clock mark.
 */
function Donut({
  slices,
  total,
}: {
  slices: { share: number; color: string }[];
  total: string;
}) {
  const R = 64;
  const C = 2 * Math.PI * R;
  let offset = 0;

  return (
    <svg
      viewBox="0 0 160 160"
      width="168"
      height="168"
      className="disp-donut"
      role="img"
      aria-label={`Disposition of ${total}`}
    >
      <g transform="rotate(-90 80 80)" fill="none" strokeWidth="24">
        {slices.map((s, i) => {
          const len = s.share * C;
          const dash = `${len} ${C - len}`;
          const el = (
            <circle
              key={i}
              cx="80"
              cy="80"
              r={R}
              stroke={s.color}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
            />
          );
          offset += len;
          return s.share > 0 ? el : null;
        })}
      </g>
      <text
        x="80"
        y="74"
        textAnchor="middle"
        fontFamily="var(--mono)"
        fontSize="8"
        letterSpacing="1.2"
        fill="var(--ink-label)"
      >
        TOTAL MOVED
      </text>
      <text
        x="80"
        y="92"
        textAnchor="middle"
        fontFamily="var(--mono)"
        fontSize="12.5"
        fill="var(--ink)"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {total}
      </text>
    </svg>
  );
}

/** A share of a total, guarded against a zero denominator. */
function share(part: number, total: number): number {
  return total > 0 ? part / total : 0;
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
      className="rpt rpt-closed"
      style={{ color: muted ? "var(--ink-muted)" : undefined }}
    >
      <span>{label}</span>
      <span className="num dim">{row?.count ?? 0}</span>
      <span className="fig" style={{ fontSize: 17 }}>
        {paise(row?.paise ?? 0)}
      </span>
    </div>
  );
}

/**
 * The T+2 journey, one dot per settlement.
 *
 * Built from a border and positioned elements rather than an SVG. The SVG
 * version drew its own labels inside a `width="100%"` viewBox, so every
 * caption scaled with the container — fine in a 480px panel, 29px-tall
 * CAPTURED once this became a full-width band.
 *
 * The dots still TRAVEL. In-transit money is money in motion, and a static
 * dot on a line says "parked", which is the opposite of the fact. The labels
 * stay where the money lands: moving them with the dot works for one
 * settlement and collides for four.
 */
function TransitTrack({
  items,
}: {
  items: { ref: string; amount_paise: number; value_date: string | null }[];
}) {
  if (items.length === 0) return null;
  return (
    <div className="track-t2">
      <div className="track-t2-ends">
        <span>Captured</span>
        <span>Expected in bank</span>
      </div>
      <div className="track-t2-line">
        {items.map((item, i) => (
          <span
            key={`dot-${item.ref}`}
            className="track-t2-dot"
            style={{
              animationDelay: `${(i * 8) / Math.max(items.length, 1)}s`,
            }}
          />
        ))}
      </div>
      <div className="track-t2-stops">
        {items.map((item, i) => (
          <div
            key={item.ref}
            className="track-t2-stop"
            style={{ left: `${(100 / (items.length + 1)) * (i + 1)}%` }}
          >
            <span className="v">{paise(item.amount_paise)}</span>
            <span className="d">{shortDate(item.value_date)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
