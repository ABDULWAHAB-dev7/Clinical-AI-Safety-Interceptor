"use client";

import { ReactLenis, useLenis } from "lenis/react";
import { useEffect, useRef, useState } from "react";

/** A hairline at the top of the page that fills as the page is read. Updated through the CSSOM, which the CSP allows. */
function Progress() {
  const bar = useRef<HTMLDivElement>(null);
  useLenis(({ progress }) => {
    bar.current?.style.setProperty("transform", `scaleX(${Math.min(Math.max(progress, 0), 1)})`);
  });
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-30 h-px bg-transparent" aria-hidden="true">
      <div ref={bar} className="progress-bar h-px w-full bg-ink" />
    </div>
  );
}

/**
 * Smooth scrolling with Lenis. It is off for people who ask for reduced motion, and the page works the same without it:
 * Lenis only changes how the browser's own scroll is eased, it does not replace it.
 */
export function SmoothScroll({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setEnabled(!query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  if (!enabled) return <>{children}</>;
  return (
    <ReactLenis root options={{ lerp: 0.1, smoothWheel: true, anchors: true }}>
      <Progress />
      {children}
    </ReactLenis>
  );
}
