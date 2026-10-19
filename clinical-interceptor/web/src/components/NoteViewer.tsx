"use client";

import { useMemo, useState } from "react";
import { buildSegments } from "@/lib/heatmap";
import { SEVERITY_ICON, SEVERITY_LABEL } from "@/lib/report";
import type { Severity, ViewSpan } from "@/lib/types";
import { SpanTooltip } from "./SpanTooltip";

const STYLE: Record<Severity, string> = {
  CRITICAL: "bg-red-100 text-red-950 underline decoration-solid decoration-2 decoration-red-700",
  WARNING: "bg-amber-100 text-amber-950 underline decoration-dotted decoration-2 decoration-amber-700",
  SAFE: "text-slate-900 underline decoration-solid decoration-2 decoration-green-700",
};
const BADGE: Record<Severity, string> = {
  CRITICAL: "text-red-900",
  WARNING: "text-amber-900",
  SAFE: "text-green-800",
};

/** The note with a highlight per attributed span. Every highlight is a button that can be reached with the keyboard. */
export function NoteViewer({ text, spans }: { text: string; spans: ViewSpan[] }) {
  const { segments, warnings } = useMemo(() => buildSegments(text, spans), [text, spans]);
  const [active, setActive] = useState<number | null>(null);

  return (
    <div className="space-y-3">
      <p className="whitespace-pre-wrap text-lg leading-9" data-testid="note-text">
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
              className={`rounded-sm px-1 py-0.5 text-left align-baseline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700 ${STYLE[severity]}`}
            >
              {segment.text}
              <span className={`ml-1 text-xs font-semibold no-underline ${BADGE[severity]}`}>
                <span aria-hidden="true">{SEVERITY_ICON[severity]} </span>
                {SEVERITY_LABEL[severity]}
              </span>
            </button>
          );
        })}
      </p>

      <div aria-live="polite" className="min-h-6 text-sm text-slate-700">
        {active === null ? "Select a highlight to see the evidence behind it." : null}
        {segments.map((segment) =>
          segment.severity === null ? null : (
            <SpanTooltip key={segment.start} id={`tip-${segment.start}`} spans={segment.spans} active={active === segment.start} />
          ),
        )}
      </div>

      {warnings.length > 0 ? (
        <p role="alert" className="rounded border border-amber-700 bg-amber-50 p-3 text-sm text-amber-950">
          {warnings.length} attribution span(s) did not match this text and are not shown:{" "}
          {warnings.map((w) => `${w.spanId} (${w.reason})`).join("; ")}
        </p>
      ) : null}
    </div>
  );
}
