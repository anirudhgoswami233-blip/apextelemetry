import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "ApexTelemetry",
  description:
    "Formula 1 telemetry, lap delta, corner and strategy analysis for race engineers.",
};

const NAV = [
  { href: "/", label: "Timing" },
  { href: "/telemetry", label: "Telemetry Lab" },
  { href: "/corners", label: "Corners" },
  { href: "/strategy", label: "Strategy" },
  { href: "/engineer", label: "Race Engineer" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-graphite-950">
        <div className="flex min-h-screen flex-col">
          <header className="sticky top-0 z-30 border-b border-graphite-700 bg-graphite-950/95 backdrop-blur">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
              <Link href="/" className="flex items-baseline gap-2">
                <span className="text-sm font-bold tracking-[0.2em] text-ink">APEX</span>
                <span className="text-sm font-light tracking-[0.2em] text-ink-faint">
                  TELEMETRY
                </span>
              </Link>

              <nav className="flex flex-wrap items-center gap-1" aria-label="Workspaces">
                {NAV.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="rounded px-2.5 py-1 text-2xs font-medium uppercase tracking-wider text-ink-muted transition-colors hover:bg-graphite-800 hover:text-ink"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>

          <main className="flex-1 p-3 md:p-4">
            <Suspense fallback={<div className="p-6 text-xs text-ink-faint">Loading workspace…</div>}>
              {children}
            </Suspense>
          </main>

          <footer className="border-t border-graphite-800 px-4 py-3">
            <p className="text-2xs leading-relaxed text-ink-faint">
              Data from OpenF1 (openf1.org). ApexTelemetry and OpenF1 are unofficial and not
              affiliated with Formula 1, the FIA or Formula One Management. OpenF1 content is
              CC BY-NC-SA 4.0 and intended for educational, personal, research and
              non-commercial use; commercial or redistributive use requires separate
              permission. Derived figures are labelled measured, calculated or estimated —
              estimates depend on stated assumptions and are not measurements.
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}
