import type { Metadata } from "next";
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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="min-h-screen">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-white focus:p-2">
          Skip to content
        </a>
        <main id="main" className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
          {children}
        </main>
      </body>
    </html>
  );
}
