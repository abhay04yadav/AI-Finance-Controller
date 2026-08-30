import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "AI Finance Controller",
  description:
    "Reconciles ledger, settlement and bank; posts the entries; reports the cash position.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          {/* Nav reads the seed off the query string, so it suspends. */}
          <Suspense fallback={<div className="topbar" style={{ minHeight: 44 }} />}>
            <Nav />
          </Suspense>
          {children}
        </div>
      </body>
    </html>
  );
}
