/**
 * Money and figure formatting. Guide §8.5.
 *
 * INDIAN GROUPING, everywhere. ₹1,50,918.37 — not ₹150,918.37. The digits group
 * in twos after the first three, and getting it wrong is the fastest way to tell
 * an Indian merchant's accountant that this tool was not built for them.
 * `Intl.NumberFormat("en-IN")` does it correctly and costs no dependency.
 *
 * Amounts cross the wire as INTEGER PAISE and are divided only here, at the
 * moment of display. The rule that holds through the whole Python side — money
 * is an int, never a float — does not get relaxed because it reached the
 * browser; it just stops mattering once nothing further is computed from it.
 */

const INR = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** `15091837` -> `1,50,918.37`. No symbol — callers place it. */
export function paise(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return INR.format(value / 100);
}

/** `15091837` -> `₹1,50,918.37`. */
export function rupees(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value < 0 ? "-" : "";
  return `${sign}₹${INR.format(Math.abs(value) / 100)}`;
}

/**
 * Percentages truncate rather than round up (§2.8).
 *
 * 99.94% must not print as 100.0%. A suspicious 100 costs more credibility than
 * an honest 99.9, and the terminal report already follows this rule — the two
 * surfaces disagreeing about the same run would be worse than either choice.
 */
export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  const factor = 10 ** digits;
  return `${(Math.floor(value * 100 * factor) / factor).toFixed(digits)}%`;
}

/**
 * A rate as the fee model states it: 1.8300%.
 *
 * Four places, because the difference between "1.83%" and "1.8300%" is the
 * difference between a round number somebody chose and a figure that was
 * measured off the merchant's own settlements (§4.2).
 */
export function rate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(4)}%`;
}

/** `2026-08-14` -> `14-Aug`, the form every frame uses. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const month = d.toLocaleString("en-GB", { month: "short" });
  return `${String(d.getDate()).padStart(2, "0")}-${month}`;
}

/** `2026-08-26T09:14:03+05:30` -> `26-Aug 09:14`. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const month = d.toLocaleString("en-GB", { month: "short" });
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${String(d.getDate()).padStart(2, "0")}-${month} ${hh}:${mm}`;
}
