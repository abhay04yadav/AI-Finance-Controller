"use client";

import type { Trace } from "@/lib/api";
import { paise, rate, rupees, shortDate } from "@/lib/money";

/**
 * The reconciliation trace. Guide §8.5 — the signature element.
 *
 *   ORD-3312 ──▶ SETL-91? ──▶ expected settlement ──▶ CREDIT LANDED
 *                            − fee @ 1.83%
 *                            − GST on fee
 *                            − ₹1,067.76 UNEXPLAINED
 *
 * **Drawn from data, not templated.** Every node, every arithmetic line and the
 * residual come from `/api/.../exceptions`; this component owns geometry and
 * nothing else. It does not know what a fee is, when T+2 falls, or why a
 * candidate was rejected — it is handed those as strings and places them.
 *
 * That split is the whole point. A trace assembled in a React component is a
 * picture of what somebody believed the pipeline did, and it goes stale the
 * first time the pipeline changes its mind. This one cannot: change the
 * matcher and the drawing changes with it, or it fails loudly.
 *
 * The layout is computed from how many rows there are, so a two-order match and
 * a four-order one both come out readable rather than one of them overlapping.
 */

const W = 940;
const LEFT = 176;      // where the source column ends
const HUB_X = 256;     // the settlement box
const HUB_W = 156;
const MATH_X = 482;    // the arithmetic column
const NOTE_X = 600;   // the note beside each arithmetic line
const OUT_X = 790;    // the landed-credit block
const OUT_W = 150;

export function TraceDiagram({ trace }: { trace: Trace }) {
  const nodes = trace.nodes;
  const steps = trace.steps;
  const unexplained = trace.outcome === "unexplained";

  // Rows are spaced so the source column and the arithmetic column both fit
  // without either dictating the other. 58px is the smallest spacing at which
  // an id, an amount and a date stack legibly at these sizes.
  const rowGap = 58;
  const nodeTop = 56;
  // Two columns with different row heights: source rows need 58px to stack an
  // id, an amount and a date; arithmetic lines need 26px. Sizing the canvas by
  // whichever column is LONGER in rows over-reserves badly when four short
  // steps sit beside two tall nodes, which is the common shape.
  const stepGap = 26;
  const height = Math.max(
    196,
    nodeTop + nodes.length * rowGap - 10,
    nodeTop + steps.length * stepGap + 30,
  );
  const hubY = Math.max(88, nodeTop + ((nodes.length - 1) * rowGap) / 2 - 10);
  const spineY = hubY + 18;

  return (
    <div className="trace">
      <div className="trace-head">
        <span className="label-sm">Reconciliation trace</span>
        {unexplained && trace.residual_paise > 0 ? (
          <span className="trace-residual">
            {rupees(trace.residual_paise)} unexplained
          </span>
        ) : (
          <span className="label-sm" style={{ color: "var(--green)" }}>
            {trace.settlement_id ?? "no settlement record"} · reconstructed
          </span>
        )}
      </div>

      <div className="trace-body">
        <svg viewBox={`0 0 ${W} ${height}`} width="100%" role="img"
             aria-label={`Reconciliation trace for ${trace.ref}`}>
          {/* -------------------------------------------------- source rows */}
          <g fontFamily="var(--mono)">
            {nodes.map((n, i) => {
              const y = nodeTop + i * rowGap;
              const dim = n.rejected;
              return (
                <g key={n.id}>
                  <text
                    x="0"
                    y={y}
                    fontSize="13"
                    fontWeight={dim ? 400 : 500}
                    fill={dim ? "var(--ink-label)" : "var(--ink)"}
                  >
                    {n.id}
                  </text>
                  <text
                    x="0"
                    y={y + 18}
                    fontSize="11.5"
                    fill={dim ? "var(--ink-faint)" : "var(--ink-label)"}
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {rupees(n.amount_paise)}
                    {n.value_date ? ` · ${shortDate(n.value_date)}` : ""}
                    {n.kind === "refund" ? " · refund" : ""}
                  </text>
                  {n.rejected && n.rejected_because ? (
                    <text x="0" y={y + 36} fontSize="10.5" fill="var(--ink-faint)">
                      {truncate(n.rejected_because, 48)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>

          {/* ------------------------------------------------ the connectors
              Each source row runs straight out to a vertical rail, and the rail
              meets the hub once. Drawing an individual elbow per row tangles as
              soon as there are more than two, which is most of them. */}
          {(() => {
            const rail = 226;
            const ys = nodes.map((_, i) => nodeTop + i * rowGap - 5);
            const top = Math.min(...ys, spineY);
            const bottom = Math.max(...ys, spineY);
            const solid = nodes.some((n) => !n.rejected);
            return (
              <g>
                <path
                  d={`M${rail} ${top} V${bottom}`}
                  stroke={solid ? "var(--ink)" : "var(--ink-ghost)"}
                  strokeWidth="1.3"
                  fill="none"
                />
                {nodes.map((n, i) => (
                  <path
                    key={`link-${n.id}-${i}`}
                    d={`M${LEFT} ${ys[i]} H${rail}`}
                    fill="none"
                    stroke={n.rejected ? "var(--ink-ghost)" : "var(--ink)"}
                    strokeWidth="1.3"
                    strokeDasharray={n.rejected ? "5 5" : undefined}
                  />
                ))}
                <path
                  d={`M${rail} ${spineY} H${HUB_X}`}
                  fill="none"
                  stroke="var(--ink)"
                  strokeWidth="1.3"
                />
                <path d={`M${HUB_X} ${spineY} l-9 -4.5 v9 z`} fill="var(--ink)" />
              </g>
            );
          })()}

          {/* the settlement hub — a question mark when nothing reported it */}
          <rect
            x={HUB_X}
            y={hubY}
            width={HUB_W}
            height="38"
            fill="var(--paper)"
            stroke={trace.settlement_known ? "var(--ink)" : "var(--ink-ghost)"}
            strokeWidth="1.3"
            strokeDasharray={trace.settlement_known ? undefined : "5 5"}
          />
          <text
            x={HUB_X + HUB_W / 2}
            y={hubY + 24}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="13"
            fontWeight="500"
            fill="var(--ink)"
          >
            {trace.settlement_id ?? "SETL-?"}
          </text>
          {!trace.settlement_known && (
            <text
              x={HUB_X + HUB_W / 2}
              y={hubY + 54}
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="10.5"
              fill="var(--ink-label)"
            >
              no settlement record
            </text>
          )}

          {/* hub -> arithmetic, arithmetic -> outcome */}
          <path
            d={`M${HUB_X + HUB_W} ${spineY} H${MATH_X - 10}`}
            fill="none"
            stroke="var(--ink)"
            strokeWidth="1.3"
          />
          <path
            d={`M${MATH_X - 10} ${spineY} l-9 -4.5 v9 z`}
            fill="var(--ink)"
            transform={`translate(9,0)`}
          />
          <path
            d={`M${NOTE_X + 120} ${spineY} H${OUT_X - 4}`}
            fill="none"
            stroke="var(--ink)"
            strokeWidth="1.3"
          />
          <path d={`M${OUT_X} ${spineY} l-9 -4.5 v9 z`} fill="var(--ink)" />

          {/* -------------------------------------------- the arithmetic */}
          <g fontFamily="var(--mono)" fontSize="12"
             style={{ fontVariantNumeric: "tabular-nums" }}>
            {steps.map((s, i) => {
              const y = nodeTop + 4 + i * stepGap;
              const residual = s.kind === "residual";
              const subtotal = s.kind === "subtotal";
              const fill = residual
                ? "var(--red)"
                : subtotal
                  ? "var(--ink)"
                  : "var(--ink)";
              const sign =
                s.kind === "open"
                  ? ""
                  : subtotal
                    ? "= "
                    : s.signed_paise < 0
                      ? "− "
                      : "+ ";
              return (
                <g key={`${s.label}-${i}`}>
                  <text
                    x={MATH_X}
                    y={y}
                    fill={fill}
                    fontWeight={subtotal || residual ? 500 : 400}
                  >
                    {sign}
                    {paise(Math.abs(s.signed_paise))}
                  </text>
                  <text
                    x={NOTE_X}
                    y={y}
                    fontSize="11"
                    fill={residual ? "var(--red)" : "var(--ink-label)"}
                  >
                    {truncate(
                      residual ? s.label.toUpperCase() : s.note || s.label,
                      42,
                    )}
                  </text>
                  {subtotal && (
                    <path
                      d={`M${MATH_X} ${y + 8} H${NOTE_X + 120}`}
                      stroke="var(--rule-key)"
                      strokeWidth="1"
                    />
                  )}
                  {residual && (
                    <path
                      d={`M${MATH_X} ${y - 20} H${NOTE_X + 120}`}
                      stroke="var(--red-rule)"
                      strokeWidth="1"
                    />
                  )}
                </g>
              );
            })}
          </g>

          {/* ------------------------------------------ where it landed */}
          <rect
            x={OUT_X}
            y={spineY - 26}
            width={OUT_W}
            height="52"
            fill={unexplained ? "var(--red)" : "var(--ink)"}
          />
          <text
            x={OUT_X + OUT_W / 2}
            y={spineY - 5}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="9.5"
            letterSpacing="1.4"
            fill={unexplained ? "#e8cdc9" : "var(--ink-ghost)"}
          >
            CREDIT LANDED
          </text>
          <text
            x={OUT_X + OUT_W / 2}
            y={spineY + 16}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="16"
            fill="var(--paper)"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {rupees(trace.credit_paise)}
          </text>
          <text
            x={OUT_X + OUT_W / 2}
            y={spineY + 46}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="10.5"
            fill="var(--ink-label)"
          >
            value {shortDate(trace.credit_value_date)}
          </text>
        </svg>

        {/* The two facts a diagram cannot carry, printed under it. */}
        <div
          className="prose-sm"
          style={{ marginTop: 12, display: "flex", gap: 24, flexWrap: "wrap" }}
        >
          {trace.fee_rate !== null && (
            <span>fee model {rate(trace.fee_rate)} inferred</span>
          )}
          {trace.open_pool_rows > 0 && (
            <span>
              open pool {trace.open_pool_rows} row
              {trace.open_pool_rows === 1 ? "" : "s"} ·{" "}
              {rupees(trace.open_pool_paise)}
            </span>
          )}
          {trace.candidates.length > 1 && (
            <span>
              {trace.candidates.length} combinations fit — none confirmed
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
