/**
 * The one place the frontend talks to the backend. Guide §5.7, §8.
 *
 * Every figure on every screen comes through here. There is no local
 * computation of a total, no derived count, no fallback constant — if a number
 * is on the page, an endpoint returned it. That is the gate-12 rule, and the
 * check for it is mechanical: start the app on seed 42, then on seed 7, and
 * anything that did not change is hardcoded.
 *
 * The seed rides along as a query parameter rather than living in a config
 * file, so switching the whole UI to another dataset is a link.
 */

export type Money = number; // always integer paise

export interface ActionOffer {
  code: string;
  label: string;
  description: string;
  posts_entry: boolean;
}

export interface TraceNode {
  id: string;
  amount_paise: Money;
  value_date: string | null;
  kind: "order" | "refund";
  settlement_id: string | null;
  rejected: boolean;
  rejected_because: string;
}

export interface TraceStep {
  label: string;
  signed_paise: Money;
  note: string;
  kind: "open" | "adjust" | "subtotal" | "residual";
}

export interface Trace {
  ref: string;
  outcome: "explained" | "unexplained";
  nodes: TraceNode[];
  steps: TraceStep[];
  settlement_id: string | null;
  settlement_known: boolean;
  credit_paise: Money;
  credit_value_date: string | null;
  residual_paise: Money;
  fee_rate: number | null;
  gst_rate: number | null;
  open_pool_rows: number;
  open_pool_paise: Money;
  candidates: string[][];
}

export interface ActionState {
  state: "acted" | "reversed";
  action_code: string;
  action_label: string;
  actor: string;
  at: string;
  detail: string;
  entry_numbers: string[];
  reversible: boolean;
}

export interface ExceptionCard {
  ref: string;
  reason_code: string;
  severity: string;
  in_transit: boolean;
  amount_paise: Money | null;
  amount: string | null;
  value_date: string | null;
  what: string;
  why: string;
  /** "model" when §4.4 job B wrote it, "classifier" when the rules did. */
  why_source: "model" | "classifier";
  open_balance_paise: Money | null;
  actions: ActionOffer[];
  trace: Trace | null;
  action_state: ActionState | null;
}

export interface ExceptionsPayload {
  run_id: string;
  label: string;
  seed: number;
  scale: number;
  closed: boolean;
  open: number;
  unreconciled_paise: Money;
  unreconciled: string;
  cleared_paise: Money;
  balance_ties: boolean;
  residual_paise: Money;
  auto_posted: number;
  pending_review: number;
  started_at: string;
  fee_rate: number | null;
  exceptions: ExceptionCard[];
  by_reason: { reason_code: string; count: number }[];
  in_transit: {
    count: number;
    total_paise: Money;
    total: string;
    ties: boolean;
    items_paise: Money;
    items: ExceptionCard[];
  };
}

export interface EntryLine {
  account: string;
  debit_paise: Money;
  credit_paise: Money;
}

export interface PreparedEntry {
  number: string | null;
  idempotency_key: string;
  entry_date: string;
  narration: string;
  lines: EntryLine[];
  total_debits_paise: Money;
  total_credits_paise: Money;
  balanced: boolean;
  source_utr: string;
  ledger_ids: string[];
  settlement_id: string | null;
  confidence: number;
  strategy: string;
}

export interface ReviewItem {
  utr: string;
  ledger_ids: string[];
  order_count: number;
  confidence: number;
  reason: string;
  amount_paise: Money;
  amount: string;
  entry_total_paise: Money;
  value_date: string;
  settlement_id: string | null;
  prepared_entry: PreparedEntry;
  decision: string | null;
}

export interface ReviewPayload {
  run_id: string;
  count: number;
  total_paise: Money;
  total: string;
  auto_post_threshold: number;
  review_threshold: number;
  items: ReviewItem[];
  decided: ReviewItem[];
  fee_rate: number | null;
  gst_rate: number;
}

export interface TieLine {
  label: string;
  paise: Money;
  amount: string;
  sign: string;
}

export interface BooksPayload {
  run_id: string;
  label: string;
  seed: number;
  posted: boolean;
  closed: boolean;
  closed_at: string | null;
  disposition: Record<string, { count: number; paise: Money; amount: string }>;
  tie_out: {
    addends: TieLine[];
    total: TieLine;
    computed_paise: Money;
    ties: boolean;
    delta_paise: Money;
  };
  in_transit: {
    count: number;
    total_paise: Money;
    total: string;
    ties: boolean;
    items: {
      ref: string;
      amount_paise: Money;
      amount: string;
      value_date: string | null;
      what: string;
    }[];
  };
  suspense: {
    paise: Money;
    amount: string;
    awaiting_approval_paise: Money;
    awaiting_approval: string;
  };
  how_it_closed: Record<string, { count: number; paise: Money }>;
  refunds_paise: Money;
  bank_ledger_total_paise: Money;
  duplicate_postings_refused: number;
}

export interface BenchmarkPayload {
  dataset: string;
  seed: number;
  scale: number;
  no_llm: boolean;
  match_precision: number;
  correct: number;
  attempted: number;
  match_rate: number;
  total: number;
  auto_resolution: number;
  auto_posted: number;
  throughput: number;
  elapsed_s: number;
  wall_ms: number;
  llm_calls: number;
  llm_cost_paise: Money;
  cost_per_100_paise: number;
  planted: number;
  anomaly_resolution: number;
  caught: number;
  resolvable_planted: number;
  resolvable_resolved: number;
  exception_recall: number;
  must_surface_planted: number;
  must_surface_flagged: number;
  genuine_misses: { ref: string; reason_code: string }[];
  false_positives: string[];
  fee_rate_inferred: number | null;
  fee_rate_planted: number | null;
  fee_rate_error: number | null;
  fee_model_summary: string;
  tiers: {
    name: string;
    label: string;
    precision: number;
    coverage: number;
    correct: number;
    attempted: number;
    empty: boolean;
  }[];
  calibration: {
    label: string;
    records: number;
    correct: number;
    precision: number;
    empty: boolean;
  }[];
  fingerprint: string;
}

export interface RunSummary {
  run_id: string;
  label: string;
  seed: number;
  scale: number;
  no_llm: boolean;
  started_at: string;
  elapsed_ms: number;
  records_processed: number;
  matches: number;
  auto_posted: number;
  pending_review: number;
  fee_rate: number | null;
  fee_model_summary: string;
  gst_rate: number;
  auto_post_threshold: number;
  review_threshold: number;
  llm_calls: number;
  adjudication_notes: string[];
  closed: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // A non-JSON error body is still an error and still gets reported: the
      // status line above already carries it. This catch improves a message,
      // it never hides a failure.
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function q(seed?: number): string {
  return seed ? `?seed=${seed}` : "";
}

export const api = {
  run: (seed?: number) => call<RunSummary>(`/api/runs/current${q(seed)}`),
  exceptions: (seed?: number) =>
    call<ExceptionsPayload>(`/api/runs/current/exceptions${q(seed)}`),
  review: (seed?: number) =>
    call<ReviewPayload>(`/api/runs/current/review${q(seed)}`),
  books: (seed?: number) =>
    call<BooksPayload>(`/api/runs/current/books${q(seed)}`),
  approve: (utr: string, seed?: number) =>
    call<{ entry_number: string | null }>(
      `/api/review/${encodeURIComponent(utr)}/approve${q(seed)}`,
      { method: "POST" },
    ),
  reject: (utr: string, seed?: number) =>
    call<{ decision: string }>(
      `/api/review/${encodeURIComponent(utr)}/reject${q(seed)}`,
      { method: "POST" },
    ),
  act: (ref: string, code: string, seed?: number) =>
    call<{ entry_numbers: string[]; detail: string }>(
      `/api/exceptions/${encodeURIComponent(ref)}/actions/${code}${q(seed)}`,
      { method: "POST" },
    ),
  undo: (ref: string, code: string, seed?: number) =>
    call<{ correcting_entries: string[]; reverses: string[] }>(
      `/api/exceptions/${encodeURIComponent(ref)}/actions/${code}/undo${q(seed)}`,
      { method: "POST" },
    ),
  close: (seed?: number) =>
    call<{ closed: boolean; closed_at: string }>(
      `/api/runs/current/close${q(seed)}`,
      { method: "POST" },
    ),
  auditTrail: (seed?: number) =>
    call<{ events: unknown[]; summary: Record<string, number> }>(
      `/api/runs/current/audit-trail${q(seed)}`,
    ),
  benchmark: (seed?: number) =>
    call<BenchmarkPayload>(`/api/benchmark${q(seed)}`, { method: "POST" }),
};
