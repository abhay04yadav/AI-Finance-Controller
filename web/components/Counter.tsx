"use client";

import { useEffect, useRef, useState } from "react";

/**
 * A figure that counts up to its value. Design 4d — "counters tick up,
 * precision resolves last".
 *
 * Motion with a job. The benchmark screen's whole claim is that the numbers
 * were computed just now rather than written down earlier, and a figure that
 * arrives already settled looks stored. Watching it resolve is the evidence.
 *
 * Two rules keep it honest:
 *
 * 1. **It always lands on the real value.** The animation is over the DISPLAY
 *    only; `value` is the API's number and the final frame is exactly it. No
 *    easing that overshoots, no rounding that leaves 99.9 sitting where 100.0
 *    belongs.
 * 2. **It respects reduced motion.** Someone who has asked their OS for less
 *    movement gets the figure immediately, not a slower version of the tick.
 */
export function Counter({
  value,
  format,
  duration = 900,
  delay = 0,
}: {
  value: number;
  format: (n: number) => string;
  duration?: number;
  delay?: number;
}) {
  const [shown, setShown] = useState(value);
  const frame = useRef<number>(0);

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || duration <= 0) {
      setShown(value);
      return;
    }

    let start: number | null = null;
    setShown(0);

    function step(now: number) {
      if (start === null) start = now;
      const elapsed = now - start - delay;
      if (elapsed < 0) {
        frame.current = requestAnimationFrame(step);
        return;
      }
      const t = Math.min(elapsed / duration, 1);
      // Ease out: fast at first, settling at the end — the shape of a
      // computation finishing, not a slider being dragged.
      const eased = 1 - (1 - t) ** 3;
      setShown(value * eased);
      if (t < 1) frame.current = requestAnimationFrame(step);
      // The last assignment is the exact value, never the eased approximation.
      else setShown(value);
    }

    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current);
  }, [value, duration, delay]);

  return <>{format(shown)}</>;
}
