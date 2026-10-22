"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/sample", label: "Sample" },
  { href: "/analyze", label: "Analyse" },
  { href: "/quarantine", label: "Quarantine" },
];

export function SiteNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main">
      <ul className="flex items-center gap-1 text-sm">
        {NAV.map((item) => {
          const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={`inline-flex rounded-full px-3.5 py-1.5 transition-colors hover:bg-black/5 ${current ? "bg-black/5 font-medium text-ink" : "text-muted hover:text-ink"}`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
