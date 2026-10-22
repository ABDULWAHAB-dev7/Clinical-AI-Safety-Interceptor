"use client";

import { useEffect, useRef } from "react";

/**
 * Fades its content up the first time it scrolls into view. Content that is already on screen is never hidden, and
 * nothing is hidden for people who ask for reduced motion or when scripts do not run, so the text is always readable.
 */
export function Reveal({
  children,
  className = "",
  delay = 0,
  as: Tag = "div",
}: {
  children: React.ReactNode;
  className?: string;
  /** Milliseconds. Staggers items that appear together. */
  delay?: number;
  as?: "div" | "section" | "li" | "article";
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (node.getBoundingClientRect().top < window.innerHeight) return; // already visible: leave it alone
    node.dataset.state = "hidden";
    if (delay) node.style.setProperty("transition-delay", `${delay}ms`);
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          node.dataset.state = "shown";
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [delay]);

  const Component = Tag as "div";
  return (
    <Component ref={ref} className={`reveal ${className}`}>
      {children}
    </Component>
  );
}
