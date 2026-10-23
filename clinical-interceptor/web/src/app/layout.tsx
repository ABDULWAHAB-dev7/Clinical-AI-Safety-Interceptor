import type { Metadata } from "next";
import Link from "next/link";
import { Geist_Mono, Inter, Source_Serif_4 } from "next/font/google";
import { Shield01Icon } from "@hugeicons/core-free-icons";
import { Icon } from "@/components/Icon";
import { SiteNav } from "@/components/SiteNav";
import { SmoothScroll } from "@/components/SmoothScroll";
import "./globals.css";

// Inter and Source Serif 4 stand in for the reference's licensed Stk Bureau faces: sans for everything functional,
// serif for the large editorial headlines only.
const sans = Inter({ variable: "--font-inter", subsets: ["latin"] });
const serif = Source_Serif_4({ variable: "--font-source-serif", subsets: ["latin"], weight: ["400", "500"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clinical AI Safety Interceptor",
  description: "Review which statements in a clinical note the patient record supports, contradicts or cannot confirm.",
};

// Every page is rendered per request, because the Content-Security-Policy nonce (src/proxy.ts) changes each time.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-paper text-ink antialiased">
        <SmoothScroll>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-40 focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-sm focus:text-paper"
          >
            Skip to content
          </a>
          <header>
            <div className="wrap flex items-center justify-between gap-2 py-4">
              <Link href="/" className="flex items-center gap-2.5 rounded-full">
                <span className="grid size-8 place-items-center rounded-full border border-ink">
                  <Icon icon={Shield01Icon} size={16} />
                </span>
                <span className="sr-only text-lg font-medium sm:not-sr-only">Interceptor</span>
              </Link>
              <SiteNav />
            </div>
          </header>

          <main id="main">{children}</main>

          <footer className="band-cream mt-28">
            <div className="wrap flex flex-col gap-3 py-10 text-xs leading-5 text-muted md:flex-row md:justify-between">
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
