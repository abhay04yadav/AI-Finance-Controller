"use client";

import { useEffect, useState } from "react";
import { api, type ExceptionsPayload } from "@/lib/api";
import { pct, rupees } from "@/lib/money";
import { useSeed } from "@/lib/useSeed";

/**
 * AI mode — what ran with no model at all. P0 design, frame 4a.
 *
 * The load-bearing claim of this project is that reconciliation is a
 * deterministic problem and the model is the last mile, not the engine. A
 * claim like that is worth nothing asserted; this panel is where it is
 * checked, so every line is the run's own count.
 *
 * The two halves are deliberately unequal in the palette as well as in the
 * numbers: blue for the layers that need no model, purple for the one that
 * does. A reader who looks at this for three seconds should come away knowing
 * which is which.
 */
export function AiMode({ onClose }: { onClose: () => void }) {
  const seed = useSeed();
  const [data, setData] = useState<ExceptionsPayload | null>(null);

  useEffect(() => {
    let live = true;
    api
      .exceptions(seed)
      .then((d) => live && setData(d))
      .catch(() => undefined);
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

  const ai = data?.ai_mode;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-label="AI mode"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <span className="label-sm">AI mode · deterministic + adjudication</span>
          <button type="button" className="more" onClick={onClose}>
            close
          </button>
        </div>

        <div className="modal-body">
          {!ai ? (
            <div className="prose-sm">reading the run…</div>
          ) : (
            <>
              <div className="label-sm" style={{ color: "var(--blue)" }}>
                Deterministic core — runs with no model at all
              </div>
              <div className="ai-layers">
                {ai.deterministic.map((l) => (
                  <div className="ai-layer" key={l.layer}>
                    <span className="tick">✓</span>
                    <span>{l.layer}</span>
                    <span className="detail">{l.detail}</span>
                  </div>
                ))}
              </div>

              <div
                className="label-sm"
                style={{ color: "var(--purple)", marginTop: 22 }}
              >
                AI adjudication — last mile only
              </div>
              <div
                className="prose-sm"
                style={{ marginTop: 8, fontFamily: "var(--mono)", lineHeight: 1.7 }}
              >
                {ai.llm_calls} call{ai.llm_calls === 1 ? "" : "s"} across{" "}
                {data.scale} records · {pct(ai.llm_share, 1)}
                <br />
                {rupees(ai.llm_cost_paise)} spent · cached, so a re-run costs
                nothing
                <br />
                {ai.adjudicated} match
                {ai.adjudicated === 1 ? "" : "es"} accepted from the model
              </div>

              {ai.notes.length > 0 && (
                <div className="prose-sm" style={{ marginTop: 12 }}>
                  {/* "The model was never asked" and "the model said no" are
                      different facts and a controller is entitled to both. */}
                  {ai.notes.map((n) => (
                    <div key={n}>· {n}</div>
                  ))}
                </div>
              )}

              <div className="ai-claim">
                The model never computes an amount, never creates a candidate
                and never posts an entry — it selects among candidates the
                deterministic layers already proved, or declines. Its verdicts
                cap one notch below the auto-post threshold, so an adjudicated
                match can never move money without a person.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
