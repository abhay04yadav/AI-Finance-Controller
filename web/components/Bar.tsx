"use client";

import { pct } from "@/lib/money";

/**
 * A stacked proportion bar, and the legend that names its parts.
 * Design 4 — "charts where a chart reads faster than a number".
 *
 * Used four times, and each time it answers a question a column of figures
 * answers slowly: where the unreconciled total actually sits (4a), how a run
 * ended up (4c), what the fee and GST are next to the cash (4c), and how hard
 * each match was (4d).
 *
 * **Segments come in as fractions computed by the API.** The bar never derives
 * a share from a rounded percentage, and never renormalises: if the shares do
 * not sum to 1 the bar is short, which is the truthful rendering of a total
 * that does not add up. A chart that silently stretches to fill its container
 * is a chart that cannot report a bug.
 */

export interface Segment {
  /** Fraction of the whole, 0..1. */
  share: number;
  color: string;
  label?: string;
  /** Shown on hover — the figure the segment stands for. */
  title?: string;
}

//: The composition ramp, in order. Largest share darkest.
export const RAMP = [
  "var(--ramp-1)",
  "var(--ramp-2)",
  "var(--ramp-3)",
  "var(--ramp-4)",
  "var(--ramp-5)",
  "var(--ramp-6)",
  "var(--ramp-7)",
  "var(--ramp-8)",
  "var(--ramp-9)",
];

/** The nth ramp colour, repeating the faintest once the ramp runs out. */
export function ramp(index: number): string {
  return RAMP[Math.min(index, RAMP.length - 1)];
}

export function Bar({
  segments,
  height = 13,
  className,
}: {
  segments: Segment[];
  height?: number;
  className?: string;
}) {
  if (segments.length === 0) return null;
  return (
    <div
      className={`bar${className ? ` ${className}` : ""}`}
      style={{ height }}
      role="img"
      aria-label={segments
        .map((s) => `${s.label ?? ""} ${pct(s.share)}`)
        .join(", ")}
    >
      {segments.map((s, i) => (
        <span
          key={`${s.label ?? i}-${i}`}
          title={s.title}
          style={{
            width: `${s.share * 100}%`,
            background: s.color,
            height,
          }}
        />
      ))}
    </div>
  );
}

export function Legend({
  items,
  trailing,
}: {
  items: { label: string; color: string; note?: string }[];
  trailing?: React.ReactNode;
}) {
  return (
    <div className="legend" style={{ marginTop: 10 }}>
      {items.map((item) => (
        <span key={item.label}>
          <span className="swatch" style={{ background: item.color }} />
          {item.label}
          {item.note && (
            <span style={{ color: "var(--ink-faint)" }}>{item.note}</span>
          )}
        </span>
      ))}
      {trailing && <span style={{ color: "var(--ink-faint)" }}>{trailing}</span>}
    </div>
  );
}

/**
 * A bar on a visible track, for one row of a small-multiples chart.
 *
 * The track matters: without it a 2% bar and a 0% bar look identical, and
 * "one record landed here" and "none did" are different findings (§7.4).
 */
export function TrackedBar({
  share,
  color,
  height = 13,
}: {
  share: number;
  color: string;
  height?: number;
}) {
  return (
    <span
      style={{ display: "block", height, background: "var(--track)" }}
      aria-hidden
    >
      <span
        className="bar"
        style={{
          display: "block",
          height,
          width: `${Math.max(share, 0) * 100}%`,
          background: color,
        }}
      />
    </span>
  );
}
