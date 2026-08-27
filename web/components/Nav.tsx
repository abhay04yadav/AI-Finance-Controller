"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type RunSummary } from "@/lib/api";
import { useSeed, withSeed } from "@/lib/useSeed";

/**
 * Four routes, and the order is the argument. Guide §8.1.
 *
 * Exceptions is first and is the index route — not a tab, not behind a summary.
 * A controller never looks at matched rows; they open the tool to find out what
 * is broken. Putting a green "59/60 matched" dashboard here would be building
 * for the 88% that needs no attention, which is building for nobody.
 */
const ROUTES = [
  { href: "/exceptions", label: "exceptions" },
  { href: "/review", label: "review" },
  { href: "/books", label: "books" },
  { href: "/benchmark", label: "benchmark" },
];

export function Nav() {
  const pathname = usePathname();
  const seed = useSeed();
  const [run, setRun] = useState<RunSummary | null>(null);

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

  return (
    <nav className="nav">
      <span className="brand">AI Finance Controller</span>
      {ROUTES.map((r) => (
        <Link
          key={r.href}
          href={withSeed(r.href, seed)}
          aria-current={pathname === r.href ? "page" : undefined}
        >
          {r.label}
        </Link>
      ))}
      <span className="spacer" />
      {/* The dataset the figures on screen belong to. On the page because two
          screenshots of two seeds are otherwise indistinguishable. */}
      <span className="seed">
        {run ? (
          <>
            seed <b>{run.seed}</b> · {run.scale} orders · {run.records_processed}{" "}
            records
          </>
        ) : (
          "connecting…"
        )}
      </span>
    </nav>
  );
}
