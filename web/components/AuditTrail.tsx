"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { stamp } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * The audit trail. P0 design, frame 4c.
 *
 * The trail's job is "who decided this, on what evidence, when", and until
 * this pass it could only answer that for the handful of rows a person
 * touched. The machine decisions are now on the same actor axis, so `system`,
 * `llm` and `user` filter against each other — which is the only way to see
 * at a glance how little of the run the model was responsible for.
 *
 * Machine lines carry the run's timestamp rather than a per-event one. That
 * is the resolution the pipeline records; giving them invented sequential
 * times to make the list look more like a log would dress an estimate as a
 * measurement, in the one artefact that exists to be trusted.
 */
type Row = {
  at: string;
  actor: string;
  kind: string;
  detail: string;
  evidence: string[];
};

const FILTERS = ["all", "system", "llm", "user"] as const;
type Filter = (typeof FILTERS)[number];

const ACTOR_COLOUR: Record<string, string> = {
  system: "var(--blue)",
  llm: "var(--purple)",
  user: "var(--gold)",
};

export function AuditTrail({ onClose }: { onClose: () => void }) {
  const seed = useSeed();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [runId, setRunId] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let live = true;
    api
      .auditTrail(seed)
      .then((t) => {
        if (!live) return;
        setRunId(t.run_id);
        const machine: Row[] = (t.decisions ?? []).map((d) => ({
          at: d.at,
          actor: d.actor,
          kind: d.actor_kind,
          detail: d.detail,
          evidence: d.evidence,
        }));
        const human: Row[] = t.events.map((e) => ({
          at: e.at,
          actor: e.actor_kind === "user" ? "user" : e.actor,
          kind: e.actor_kind ?? "user",
          detail: e.detail,
          evidence: [],
        }));
        // Machine decisions first, then what a person did to them — the order
        // the run actually happened in.
        setRows([...machine, ...human]);
      })
      .catch(() => setRows([]));
    return () => {
      live = false;
    };
  }, [seed]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const shown = useMemo(
    () => (rows ?? []).filter((r) => filter === "all" || r.kind === filter),
    [rows, filter],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows?.length ?? 0 };
    for (const r of rows ?? []) c[r.kind] = (c[r.kind] ?? 0) + 1;
    return c;
  }, [rows]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal modal-wide"
        role="dialog"
        aria-label="Audit trail"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <span className="label-sm">
            Audit trail{" "}
            <span style={{ fontWeight: 400, color: "var(--ink-label)" }}>
              · run {runId}
            </span>
          </span>
          <button type="button" className="more" onClick={onClose}>
            close
          </button>
        </div>

        <div className="audit-filters">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className="chip"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {f} <span className="n">{counts[f] ?? 0}</span>
            </button>
          ))}
        </div>

        <div className="modal-body">
          {rows === null ? (
            <div className="prose-sm">reading the trail…</div>
          ) : shown.length === 0 ? (
            <div className="prose-sm">
              Nothing under this filter. {filter === "user" && "Nobody has had to intervene in this run."}
            </div>
          ) : (
            <div className="audit-rows">
              {shown.map((r, i) => (
                <div className="audit-row" key={`${r.actor}-${r.detail}-${i}`}>
                  <span className="at">{stamp(r.at)}</span>
                  <span
                    className="actor"
                    style={{ color: ACTOR_COLOUR[r.kind] ?? "var(--ink-muted)" }}
                  >
                    {r.actor}
                  </span>
                  <span>
                    {r.detail}
                    {r.evidence.length > 0 && (
                      <div className="evidence">
                        evidence: {r.evidence.join(", ")}
                      </div>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="audit-foot">
          Every line comes from the run&rsquo;s own record — who decided this,
          on what evidence, when.
        </div>
      </div>
    </div>
  );
}
