"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/sample", label: "Sample" },
  { href: "/quarantine", label: "Quarantine" },
];

/** Ghost text links, and the one filled action. The current page gets the same color at a heavier weight. */
export function SiteNav() {
  const pathname = usePathname();
  const on = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  return (
    <nav aria-label="Main">
      <ul className="flex items-center gap-0.5 text-sm sm:gap-2 sm:text-base">
        {LINKS.map((item) => (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={on(item.href) ? "page" : undefined}
              className={`inline-flex rounded-full px-2.5 py-2 hover:bg-cream sm:px-3 ${on(item.href) ? "font-medium" : ""}`}
            >
              {item.label}
            </Link>
          </li>
        ))}
        <li className="ml-1 sm:ml-3">
          <Link href="/analyze" aria-current={on("/analyze") ? "page" : undefined} className="btn btn-solid !px-4 !py-2 text-sm sm:!px-5 sm:!text-base">
            Analyse
          </Link>
        </li>
      </ul>
    </nav>
  );
}
