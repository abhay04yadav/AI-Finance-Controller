"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { AiMode } from "@/components/AiMode";
import { api, type RunSummary } from "@/lib/api";
import { useSeed, withSeed } from "@/lib/useSeed";

/**
 * The dark bar across the top. P0 design, frame 4a.
 *
 * Four routes, and the order is the argument. Exceptions is first and is the
 * index route — not a tab, not behind a summary. A controller never looks at
 * matched rows; they open the tool to find out what is broken.
 *
 * **Every route carries its own count.** The point of a count on a tab is that
 * you can see there is work waiting without opening the screen, so each one is
 * the same number that screen would show — `open_exceptions` is counted on the
 * server exactly the way `/exceptions` counts it, rows already acted on
 * excluded. A tab that said 9 over a page showing 7 would be worse than a tab
 * with no number at all.
 *
 * **AI MODE is a link, not a badge.** The claim this project makes is that the
 * deterministic core does the work and the model handles the last mile. That
 * claim is checkable, so the bar opens the panel that checks it rather than
 * asserting it in a tooltip.
 */
const ROUTES = [
  { href: "/exceptions", label: "exceptions" },
  { href: "/review", label: "review" },
  { href: "/books", label: "books" },
  { href: "/benchmark", label: "benchmark" },
] as const;

export function Nav() {
  const pathname = usePathname();
  const seed = useSeed();
  const [run, setRun] = useState<RunSummary | null>(null);
  const [ai, setAi] = useState(false);
  const [stuck, setStuck] = useState(false);

  // The bar is sticky in CSS; this only decides whether it casts a shadow.
  // Drawing one unconditionally puts a shadow over nothing on a page that has
  // not been scrolled, which reads as a rendering artefact rather than depth.
  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    let live = true;
    api
      .run(seed)
      .then((r) => live && setRun(r))
      .catch(() => live && setRun(null));
    return () => {
      live = false;
    };
  }, [seed]);

  function countFor(href: string): string | null {
    if (!run) return null;
    if (href === "/exceptions") return String(run.open_exceptions);
    if (href === "/review") return String(run.pending_review);
    if (href === "/books") return run.closed ? "closed" : "open";
    if (href === "/benchmark") return "✓";
    return null;
  }

  return (
    <>
      {ai && <AiMode onClose={() => setAi(false)} />}
      <nav className={`topbar ${stuck ? "topbar-stuck" : ""}`}>
        <div className="topbar-routes">
          {ROUTES.map((r) => {
            const here = pathname === r.href;
            const count = countFor(r.href);
            return (
              <Link
                key={r.href}
                href={withSeed(r.href, seed)}
                className={`topbar-route ${here ? "on" : ""}`}
                aria-current={here ? "page" : undefined}
              >
                {here && <span className="topbar-dot" />}
                {r.label}
                {count !== null && <span className="n">{count}</span>}
              </Link>
            );
          })}
        </div>

        {/* The dataset the figures on screen belong to. On the page because
            two screenshots of two seeds are otherwise indistinguishable. */}
        <div className="topbar-meta">
          {run ? (
            <>
              <span>
                seed <b>{run.seed}</b>
              </span>
              <span className="sep">·</span>
              <span>{run.records_processed} records</span>
              <span className="sep">·</span>
              <button
                type="button"
                className="topbar-ai"
                onClick={() => setAi(true)}
              >
                AI mode
              </button>
              <span className="sep">·</span>
              <span style={{ color: run.closed ? "var(--green)" : "var(--gold)" }}>
                {run.closed ? "books closed" : "books open"}
              </span>
            </>
          ) : (
            <span>connecting…</span>
          )}
        </div>
      </nav>
    </>
  );
}
