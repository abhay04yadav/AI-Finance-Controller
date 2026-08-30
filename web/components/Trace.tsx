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
 *
 * **It assembles rather than appears.** The diagram draws itself in the order
 * the money actually moved — sources gather onto a rail, the rail reaches the
 * settlement, the settlement is taken apart into arithmetic, and the
 * arithmetic lands as a credit in the bank. That sequence is the one thing a
 * static picture of this cannot say: which way the causation runs. Read it
 * once and you know the ledger rows are the input and the credit is the
 * consequence, not the other way round.
 *
 * The whole assembly is ~1.3s and every element is at full opacity by then.
 * It is deliberately short: the card opens under j/k triage, so a reader
 * walking seven exceptions watches this seven times.
 */

/* The canvas, as columns that abut rather than as points chosen by eye.
 *
 * Every x below is derived from the one before it, so a column cannot
 * silently overlap its neighbour — which is exactly what happened when they
 * were independent constants: the notes were truncated to 42 characters
 * (~277 units) inside a 190-unit gap and ran under the outcome box.
 *
 * IBM Plex Mono advances 0.6em per character. That ratio is what turns "this
 * column is 204 units wide" into "this text may be 30 characters", so the
 * truncation tracks the layout instead of being a number somebody tried. */
const ADVANCE = 0.6;

const SRC_W = 176;     // the source column: ids, amounts, rejection notes
const LEFT = SRC_W;    // where a connector leaves its source row
const RAIL = 226;      // the vertical the sources gather onto

const HUB_X = 256;     // the settlement box
const HUB_W = 156;
const HUB_R = HUB_X + HUB_W;

const MATH_X = 470;    // the arithmetic column: signed amounts
const MATH_W = 116;
const NOTE_X = MATH_X + MATH_W;  // what each line means, beside it
const NOTE_W = 204;
const BLOCK_R = NOTE_X + NOTE_W; // the right edge of the arithmetic block

const OUT_X = BLOCK_R + 40;      // the landed-credit block
const OUT_W = 150;
const W = OUT_X + OUT_W;

/** How many characters of `size`-px mono fit in `width` units. */
function fits(width: number, size: number): number {
  return Math.max(6, Math.floor(width / (size * ADVANCE)));
}

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
  const hubY = Math.max(88, nodeTop + ((nodes.length - 1) * rowGap) / 2 - 10);
  const spineY = hubY + 18;

  // The arithmetic is CENTRED on the spine, not hung from the top of the
  // canvas. Top-aligning it put the block's last line above the spine on a
  // five-source trace, so the arrow into the landed credit left from empty
  // space below the sums — the diagram read as two unrelated halves at
  // different heights. Everything on the spine now sits on the spine.
  const mathTop = spineY - ((steps.length - 1) * stepGap) / 2;

  const height = Math.max(
    196,
    nodeTop + nodes.length * rowGap - 10,
    mathTop + steps.length * stepGap + 30,
  );

  // The schedule, in seconds, derived from the shape of THIS trace rather
  // than hard-coded — a one-source trace should not sit through the pause a
  // five-source trace needs to gather. Stages overlap slightly on purpose:
  // butted end to end the diagram reads as five separate events instead of
  // one continuous movement.
  const lastLink = 0.07 + Math.max(nodes.length - 1, 0) * 0.032;
  const at = {
    node: (i: number) => 0.02 + i * 0.032,
    rail: 0.07,
    link: (i: number) => 0.07 + i * 0.032,
    toHub: lastLink + 0.22,
    hub: lastLink + 0.3,
    toMath: lastLink + 0.46,
    step: (i: number) => lastLink + 0.6 + i * 0.042,
    toOut: lastLink + 0.6 + steps.length * 0.042,
    out: lastLink + 0.7 + steps.length * 0.042,
  };
  const delay = (s: number) => ({ animationDelay: `${s.toFixed(3)}s` });

  return (
    <div className="trace">
      <div className="trace-head">
        <span className="label-sm">Reconciliation trace</span>
        {/* Three outcomes, not two. Branching on the residual alone put a
            green "reconstructed" on the AMBIGUOUS rows — their residual is
            zero because the arithmetic ties, several times over, and the
            unresolved part is the choosing. A green header directly above a
            red UNRESOLVED block is the screen contradicting itself. */}
        {trace.residual_paise > 0 ? (
          <span className="trace-residual">
            {rupees(trace.residual_paise)} unexplained
          </span>
        ) : unexplained ? (
          <span className="label-sm" style={{ color: "var(--gold)" }}>
            {trace.candidates.length > 1
              ? `${trace.candidates.length} candidates · none confirmed`
              : "not matched"}
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
                <g key={n.id} className="t-in" style={delay(at.node(i))}>
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
                      {truncate(n.rejected_because, fits(SRC_W, 10.5))}
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
            const rail = RAIL;
            const ys = nodes.map((_, i) => nodeTop + i * rowGap - 5);
            const top = Math.min(...ys, spineY);
            const bottom = Math.max(...ys, spineY);
            const solid = nodes.some((n) => !n.rejected);
            return (
              <g>
                <path
                  d={`M${rail} ${top} V${bottom}`}
                  pathLength="1"
                  className="t-line"
                  style={delay(at.rail)}
                  stroke={solid ? "var(--ink)" : "var(--ink-ghost)"}
                  strokeWidth="1.3"
                  fill="none"
                />
                {nodes.map((n, i) => (
                  /* A rejected candidate keeps its dashed stroke, so it cannot
                     also carry the drawing dash. It fades in on the same beat
                     instead — still sequenced, just not drawn. */
                  <path
                    key={`link-${n.id}-${i}`}
                    d={`M${LEFT} ${ys[i]} H${rail}`}
                    pathLength="1"
                    className={n.rejected ? "t-in" : "t-line"}
                    style={delay(at.link(i))}
                    fill="none"
                    stroke={n.rejected ? "var(--ink-ghost)" : "var(--ink)"}
                    strokeWidth="1.3"
                    strokeDasharray={n.rejected ? "5 5" : undefined}
                  />
                ))}
                <path
                  d={`M${rail} ${spineY} H${HUB_X}`}
                  pathLength="1"
                  className="t-line"
                  style={delay(at.toHub)}
                  fill="none"
                  stroke="var(--ink)"
                  strokeWidth="1.3"
                />
                <path
                  d={`M${HUB_X} ${spineY} l-9 -4.5 v9 z`}
                  className="t-in"
                  style={delay(at.hub)}
                  fill="var(--ink)"
                />
              </g>
            );
          })()}

          {/* the settlement hub — a question mark when nothing reported it */}
          <rect
            x={HUB_X}
            y={hubY}
            width={HUB_W}
            height="38"
            className="t-pop"
            style={delay(at.hub)}
            fill="var(--paper)"
            stroke={trace.settlement_known ? "var(--ink)" : "var(--ink-ghost)"}
            strokeWidth="1.3"
            strokeDasharray={trace.settlement_known ? undefined : "5 5"}
          />
          <text
            className="t-pop"
            style={delay(at.hub)}
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
              className="t-pop"
              style={delay(at.hub)}
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
            d={`M${HUB_R} ${spineY} H${MATH_X - 10}`}
            pathLength="1"
            className="t-line"
            style={delay(at.toMath)}
            fill="none"
            stroke="var(--ink)"
            strokeWidth="1.3"
          />
          <path
            d={`M${MATH_X - 10} ${spineY} l-9 -4.5 v9 z`}
            className="t-in"
            style={delay(at.toMath + 0.16)}
            fill="var(--ink)"
            transform={`translate(9,0)`}
          />
          <path
            d={`M${BLOCK_R + 8} ${spineY} H${OUT_X - 4}`}
            pathLength="1"
            className="t-line"
            style={delay(at.toOut)}
            fill="none"
            stroke="var(--ink)"
            strokeWidth="1.3"
          />
          <path
            d={`M${OUT_X} ${spineY} l-9 -4.5 v9 z`}
            className="t-in"
            style={delay(at.toOut + 0.16)}
            fill="var(--ink)"
          />

          {/* -------------------------------------------- the arithmetic */}
          <g fontFamily="var(--mono)" fontSize="12"
             style={{ fontVariantNumeric: "tabular-nums" }}>
            {steps.map((s, i) => {
              const y = mathTop + i * stepGap;
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
                <g
                  key={`${s.label}-${i}`}
                  className="t-in"
                  style={delay(at.step(i))}
                >
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
                      fits(NOTE_W, 11),
                    )}
                  </text>
                  {subtotal && (
                    <path
                      d={`M${MATH_X} ${y + 8} H${BLOCK_R}`}
                      stroke="var(--rule-key)"
                      strokeWidth="1"
                    />
                  )}
                  {residual && (
                    <path
                      d={`M${MATH_X} ${y - 20} H${BLOCK_R}`}
                      stroke="var(--red-rule)"
                      strokeWidth="1"
                    />
                  )}
                </g>
              );
            })}
          </g>

          {/* ------------------------------------------ where it landed */}
          {/* Ink, always. The credit landing is a FACT — it is the one thing
              on this diagram that definitely happened — and colouring it red
              when the reconciliation failed made the same box mean two
              different things on two rows of the same list. What went wrong
              is carried by the residual line, the header, and the block
              beneath; the amount that arrived is just the amount that
              arrived. */}
          <rect
            x={OUT_X}
            y={spineY - 26}
            width={OUT_W}
            height="52"
            className="t-pop"
            style={delay(at.out)}
            fill="var(--ink)"
          />
          <text
            className="t-pop"
            style={delay(at.out)}
            x={OUT_X + OUT_W / 2}
            y={spineY - 5}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="9.5"
            letterSpacing="1.4"
            fill="var(--ink-ghost)"
          >
            CREDIT LANDED
          </text>
          <text
            className="t-pop"
            style={{ ...delay(at.out), fontVariantNumeric: "tabular-nums" }}
            x={OUT_X + OUT_W / 2}
            y={spineY + 16}
            textAnchor="middle"
            fontFamily="var(--mono)"
            fontSize="16"
            fill="var(--paper)"
          >
            {rupees(trace.credit_paise)}
          </text>
          <text
            className="t-pop"
            style={delay(at.out)}
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
