"use client";

import { useState } from "react";

/**
 * A term a controller may not know, with its meaning one hover away.
 * Design 4a: "hover any dotted term".
 *
 * The point is not decoration. This tool is read by someone who understands
 * accounting and may not understand payments — they know what a suspense
 * account is and have never heard of an MDR slab. Every jargon word on these
 * screens is either explained here or should not be on screen.
 *
 * Definitions live in this file rather than the API because they are about the
 * DOMAIN, not about the run: "UTR" means the same thing on every seed, in every
 * dataset, forever. Nothing here is a figure, and no figure is ever rendered
 * through it — that rule is unchanged.
 */

export type Term =
  | "recon"
  | "UTR"
  | "GST"
  | "MDR"
  | "T2"
  | "settlement"
  | "journal"
  | "suspense"
  | "precision"
  | "autores";

const GLOSSARY: Record<Term, { title: string; body: string }> = {
  recon: {
    title: "Reconciliation",
    body: "Proving that three separate records of the same money agree: what you sold, what the payment gateway says it settled, and what the bank actually paid you.",
  },
  UTR: {
    title: "UTR",
    body: "Unique Transaction Reference — the number a bank puts on a transfer. It identifies the payment on the statement, but it carries no list of which orders were inside it.",
  },
  GST: {
    title: "GST input credit",
    body: "The tax charged on the gateway's fee. You can reclaim it, so it is posted to its own account rather than folded into the fee — folded in, it is money quietly given away.",
  },
  MDR: {
    title: "MDR",
    body: "Merchant Discount Rate — the percentage the gateway keeps from each sale. It varies by card type, so an international card can be charged at a higher slab than the rate we infer.",
  },
  T2: {
    title: "T+2",
    body: "Two business days. A card payment captured on Monday reaches the bank on Wednesday, so money captured recently is expected to be missing.",
  },
  settlement: {
    title: "Settlement",
    body: "One payout from the gateway, bundling many orders into a single bank transfer. The bundle is why a credit rarely equals any one order.",
  },
  journal: {
    title: "Journal entry",
    body: "One balanced bookkeeping record: every debit matched by a credit. Nothing enters the books except through one, and none of them can be edited afterwards.",
  },
  suspense: {
    title: "Suspense account",
    body: "A named account holding money that arrived but cannot yet be explained. Parking it there keeps the books equal to the bank while a person works out where it belongs.",
  },
  precision: {
    title: "Match precision",
    body: "Of the payments the system claimed to explain, the share it got exactly right. Different from match rate, which counts how many it attempted at all.",
  },
  autores: {
    title: "Auto-resolution",
    body: "The share of payments that finished with nobody looking at them. This is the number that decides how much of a controller's day the tool gives back.",
  },
};

export function Gloss({
  term,
  children,
  teal,
}: {
  term: Term;
  children: React.ReactNode;
  teal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const entry = GLOSSARY[term];

  return (
    <span
      className={`term${teal ? " term-teal" : ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      // Reachable without a mouse: the definition is part of the content, not
      // a hover flourish, so it has to open on focus too.
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={0}
      role="button"
      aria-describedby={open ? `gloss-${term}` : undefined}
    >
      {children}
      {open && (
        <span className="gloss" id={`gloss-${term}`} role="tooltip">
          <b>{entry.title}</b>
          {entry.body}
        </span>
      )}
    </span>
  );
}
