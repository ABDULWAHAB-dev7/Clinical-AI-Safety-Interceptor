import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import { Shield01Icon } from "@hugeicons/core-free-icons";
import { Icon } from "@/components/Icon";
import { SiteNav } from "@/components/SiteNav";
import { SmoothScroll } from "@/components/SmoothScroll";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clinical AI Safety Interceptor",
  description: "Review which statements in a clinical note the patient record supports, contradicts or cannot confirm.",
};

// Every page is rendered per request, because the Content-Security-Policy nonce (src/proxy.ts) changes each time.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="min-h-screen bg-paper text-ink antialiased">
        <SmoothScroll>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-40 focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-sm focus:text-paper"
          >
            Skip to content
          </a>
          <header className="sticky top-0 z-20 border-b border-line bg-paper/85 backdrop-blur">
            <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-3.5 sm:px-8">
              <Link href="/" className="flex items-center gap-2.5 rounded-full">
                <span className="grid size-8 place-items-center rounded-full border border-ink">
                  <Icon icon={Shield01Icon} size={16} />
                </span>
                <span className="sr-only text-sm font-medium tracking-tight sm:not-sr-only">Interceptor</span>
              </Link>
              <SiteNav />
            </div>
          </header>

          <main id="main" className="mx-auto max-w-5xl px-5 sm:px-8">
            {children}
          </main>

          <footer className="mt-32 border-t border-line">
            <div className="mx-auto flex max-w-5xl flex-col gap-3 px-5 py-8 text-xs leading-5 text-muted sm:px-8 md:flex-row md:justify-between">
              <p>Clinical AI Safety Interceptor</p>
              <p className="max-w-xl md:text-right">
                Synthetic data only. Not validated for clinical use, not a medical device, and not for patient care. Nothing
                pasted here is stored by this application.
              </p>
            </div>
          </footer>
        </SmoothScroll>
      </body>
    </html>
  );
}
