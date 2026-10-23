import { useId } from "react";

// Decorative backgrounds that echo the product: lines of a note being read, a pulse line, rings of verification, and a
// field of dots. They are inline SVG with presentation attributes only (no style attributes, so the strict CSP is fine),
// they are hidden from assistive technology, and they sit behind the content at low contrast.

const ROWS = [
  { w: 220, mark: false },
  { w: 160, mark: true },
  { w: 250, mark: false },
  { w: 190, mark: false },
  { w: 130, mark: true },
  { w: 240, mark: false },
  { w: 170, mark: false },
  { w: 210, mark: false },
  { w: 120, mark: true },
];

function NoteLines({ x, flip }: { x: number; flip?: boolean }) {
  return (
    <g>
      {ROWS.map((row, i) => (
        <rect
          key={i}
          x={flip ? x - row.w : x}
          y={90 + i * 38}
          width={row.w}
          height={9}
          rx={4.5}
          fill={row.mark ? "#97cde5" : "#dddcdd"}
          opacity={row.mark ? 0.7 : 0.65}
        />
      ))}
    </g>
  );
}

/** Behind the hero: faint lines of text with a few marked, two soft washes, and a pulse line along the bottom. */
export function HeroBackdrop() {
  return (
    <svg
      className="pointer-events-none absolute inset-0 -z-10 size-full opacity-60 sm:opacity-100"
      viewBox="0 0 1200 560"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id="hero-sky" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#97cde5" stopOpacity="0.42" />
          <stop offset="1" stopColor="#97cde5" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hero-sage" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#c8dfaa" stopOpacity="0.5" />
          <stop offset="1" stopColor="#c8dfaa" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="170" cy="130" r="330" fill="url(#hero-sky)" />
      <circle cx="1060" cy="400" r="320" fill="url(#hero-sage)" />
      <NoteLines x={36} />
      <NoteLines x={1164} flip />
      <path
        d="M0 500 H430 l14 -8 l12 8 l10 -34 l14 66 l12 -40 l10 8 H1200"
        fill="none"
        stroke="#cbcbcb"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A heartbeat line used as a section divider, with the spike drawn in sky blue. */
export function PulseLine({ className = "" }: { className?: string }) {
  return (
    <svg className={`pointer-events-none w-full ${className}`} viewBox="0 0 1200 64" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d="M0 32 H520 l14 -6 l12 6 H1200" fill="none" stroke="#cbcbcb" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <path d="M546 32 l10 -26 l14 52 l10 -32 l8 6" fill="none" stroke="#97cde5" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <path d="M600 32 H1200" fill="none" stroke="#cbcbcb" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Concentric rings, like a scan closing in on a finding. White on cream, with one sky and one sage marker. */
export function Rings({ className = "" }: { className?: string }) {
  return (
    <svg className={`pointer-events-none ${className}`} viewBox="0 0 600 600" aria-hidden="true" focusable="false">
      {[90, 150, 210, 270].map((r) => (
        <circle key={r} cx="300" cy="300" r={r} fill="none" stroke="#ffffff" strokeWidth="2" />
      ))}
      <circle cx="300" cy="300" r="26" fill="#ffffff" />
      <circle cx="300" cy="300" r="9" fill="#97cde5" />
      <circle cx="438" cy="162" r="9" fill="#c8dfaa" />
      <circle cx="141" cy="428" r="7" fill="#ffffff" />
    </svg>
  );
}

/** A field of dots that fades out toward its edges. */
export function DotField({ className = "" }: { className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={`pointer-events-none ${className}`} width="100%" height="100%" aria-hidden="true" focusable="false">
      <defs>
        <pattern id={`dots-${id}`} width="26" height="26" patternUnits="userSpaceOnUse">
          <circle cx="2" cy="2" r="1.6" fill="#cbcbcb" />
        </pattern>
        <radialGradient id={`fade-${id}`} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#000000" />
        </radialGradient>
        <mask id={`mask-${id}`}>
          <rect width="100%" height="100%" fill={`url(#fade-${id})`} />
        </mask>
      </defs>
      <rect width="100%" height="100%" fill={`url(#dots-${id})`} mask={`url(#mask-${id})`} />
    </svg>
  );
}
