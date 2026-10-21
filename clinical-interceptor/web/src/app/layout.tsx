import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clinical AI Safety Interceptor",
  description: "Review which statements in a clinical note the patient record supports, contradicts or cannot confirm.",
};

// Every page is rendered per request, because the Content-Security-Policy nonce (src/proxy.ts) changes each time.
export const dynamic = "force-dynamic";

const NAV = [
  { href: "/", label: "Sample review" },
  { href: "/analyze", label: "Analyse a note" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-20 focus:rounded-lg focus:bg-white focus:p-2 focus:shadow"
        >
          Skip to content
        </a>
        <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2.5 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
              <span className="grid size-9 place-items-center rounded-xl bg-indigo-600 text-white shadow-sm" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z" />
                  <path d="M9 12l2 2 4-4" />
                </svg>
              </span>
              <span className="leading-tight">
                <span className="block text-sm font-semibold">Clinical AI Safety Interceptor</span>
                <span className="block text-xs text-slate-600">Verifies generated text against the record</span>
              </span>
            </Link>
            <nav aria-label="Main">
              <ul className="flex gap-1 text-sm font-medium">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className="inline-flex rounded-lg px-3 py-2 text-slate-700 transition-colors hover:bg-slate-100 hover:text-slate-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
          {children}
        </main>

        <footer className="border-t border-slate-200 bg-white">
          <p className="mx-auto max-w-6xl px-4 py-6 text-xs leading-5 text-slate-600 sm:px-6">
            Synthetic data only. Not validated for clinical use, not a medical device, and not for patient care. Nothing
            pasted here is stored by this application.
          </p>
        </footer>
      </body>
    </html>
  );
}
