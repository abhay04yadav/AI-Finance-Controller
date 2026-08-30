"use client";

import { useEffect, useState } from "react";

/**
 * "What am I looking at? · 4 steps". Design 4a.
 *
 * Four sentences explaining the screen to someone seeing it for the first
 * time. Two rules about it, both from the design:
 *
 * 1. **It is opened, never sprung.** No auto-open on first visit, no
 *    localStorage flag deciding whether you have earned the screen yet. The
 *    header carries a quiet link and the tour appears when asked. A controller
 *    who opens this tool every morning should never have to dismiss anything.
 * 2. **It explains the SCREEN, not the product.** Each step points at
 *    something visible — the column, the rows, the section below the rule.
 *
 * Escape closes it, and focus is not trapped: it is an explanation, not a
 * transaction, and trapping someone inside an explanation is hostile.
 */

const STEPS = [
  "Three records that never agree — the ledger, the gateway and the bank.",
  "This column is what's still unexplained. It counts down to zero.",
  "Each row is one thing the system couldn't resolve, and why.",
  "In transit isn't a problem — that money just hasn't arrived yet.",
];

export function Tour({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") onClose();
      if (ev.key === "Enter" || ev.key === "ArrowRight") next();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function next() {
    if (step >= STEPS.length - 1) onClose();
    else setStep((s) => s + 1);
  }

  const last = step === STEPS.length - 1;

  return (
    <div
      className="tour-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="What am I looking at"
    >
      <div className="tour-card" onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
          }}
        >
          <div
            className="label-sm"
            style={{ letterSpacing: "0.16em", fontSize: 10 }}
          >
            Step {step + 1} of {STEPS.length}
          </div>
          <button
            className="more"
            onClick={onClose}
            style={{ fontStyle: "normal", color: "var(--ink-label)" }}
          >
            skip
          </button>
        </div>

        <div className="tour-step">{STEPS[step]}</div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginTop: 28,
          }}
        >
          <div style={{ display: "flex", gap: 5 }}>
            {STEPS.map((_, i) => (
              <span
                key={i}
                className={`tour-pip${i <= step ? " tour-pip-on" : ""}`}
              />
            ))}
          </div>
          <button className="btn" onClick={next} style={{ padding: "10px 18px" }}>
            {last ? "Done" : "Next"}
          </button>
        </div>

        <div
          className="prose-sm"
          style={{ marginTop: 18, fontStyle: "italic" }}
        >
          Reopen any time from “What am I looking at?” in the header.
        </div>
      </div>
    </div>
  );
}
