"use client";

import { useMemo, useState } from "react";
import { buildSegments } from "@/lib/heatmap";
import { SEVERITY_ICON, SEVERITY_LABEL } from "@/lib/report";
import type { Severity, ViewSpan } from "@/lib/types";
import { SpanTooltip } from "./SpanTooltip";

// Each severity has a color, an underline style, and a text label, so color is never the only signal.
const STYLE: Record<Severity, string> = {
  CRITICAL: "bg-red-100/80 text-red-950 underline decoration-solid decoration-2 decoration-red-700 hover:bg-red-200/80",
  WARNING: "bg-amber-100/80 text-amber-950 underline decoration-dotted decoration-2 decoration-amber-700 hover:bg-amber-200/80",
  SAFE: "bg-emerald-50 text-slate-900 underline decoration-solid decoration-2 decoration-emerald-700 hover:bg-emerald-100",
};
const BADGE: Record<Severity, string> = {
  CRITICAL: "bg-red-700 text-white",
  WARNING: "bg-amber-700 text-white",
  SAFE: "bg-emerald-700 text-white",
};

/** The note with a highlight per attributed span. Every highlight is a button that can be reached with the keyboard. */
export function NoteViewer({ text, spans }: { text: string; spans: ViewSpan[] }) {
  const { segments, warnings } = useMemo(() => buildSegments(text, spans), [text, spans]);
  const [active, setActive] = useState<number | null>(null);

  return (
    <div className="space-y-4">
      <p className="whitespace-pre-wrap rounded-xl bg-slate-50 p-5 font-sans text-base leading-8 text-slate-800 sm:p-6 sm:text-lg sm:leading-9" data-testid="note-text">
        {segments.map((segment) => {
          if (segment.severity === null) return <span key={segment.start}>{segment.text}</span>;
          const severity = segment.severity;
          const tipId = `tip-${segment.start}`;
          return (
            <button
              key={segment.start}
              type="button"
              data-severity={severity}
              aria-describedby={tipId}
              aria-expanded={active === segment.start}
              onClick={() => setActive(active === segment.start ? null : segment.start)}
              onFocus={() => setActive(segment.start)}
              onMouseEnter={() => setActive(segment.start)}
              className={`mx-0.5 inline-flex flex-wrap items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left align-baseline transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 ${STYLE[severity]} ${active === segment.start ? "ring-2 ring-indigo-600" : ""}`}
            >
              {segment.text}
              <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold no-underline ${BADGE[severity]}`}>
                <span aria-hidden="true">{SEVERITY_ICON[severity]}</span>
                {SEVERITY_LABEL[severity]}
              </span>
            </button>
          );
        })}
      </p>

      <div aria-live="polite" className="min-h-6 text-sm text-slate-600">
        {active === null ? "Select a highlight to see the evidence behind it." : null}
        {segments.map((segment) =>
          segment.severity === null ? null : (
            <SpanTooltip key={segment.start} id={`tip-${segment.start}`} spans={segment.spans} active={active === segment.start} />
          ),
        )}
      </div>

      {warnings.length > 0 ? (
        <p role="alert" className="rounded-xl border border-amber-700 bg-amber-50 p-4 text-sm text-amber-950">
          {warnings.length} attribution span(s) did not match this text and are not shown:{" "}
          {warnings.map((w) => `${w.spanId} (${w.reason})`).join("; ")}
        </p>
      ) : null}
    </div>
  );
}
