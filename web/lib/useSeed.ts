"use client";

import { useSearchParams } from "next/navigation";

/**
 * The seed the whole UI is looking at, read from `?seed=`.
 *
 * This is how gate 12 is verified: open the app on seed 42, then on seed 7, and
 * every figure on every screen must change. Making that a query parameter
 * rather than a restart means the check takes two clicks, and means a judge can
 * do it themselves without being told how.
 *
 * `undefined` means "whatever the API was started with" (AFC_SEED, default 42).
 * The hook deliberately does not substitute 42 itself — the frontend inventing
 * a default is exactly the kind of hardcoded figure this gate is about.
 */
export function useSeed(): number | undefined {
  const params = useSearchParams();
  const raw = params.get("seed");
  if (!raw) return undefined;
  const seed = Number.parseInt(raw, 10);
  return Number.isFinite(seed) ? seed : undefined;
}

/** Keep the seed on a link, so navigation never silently changes dataset. */
export function withSeed(href: string, seed: number | undefined): string {
  return seed ? `${href}?seed=${seed}` : href;
}
