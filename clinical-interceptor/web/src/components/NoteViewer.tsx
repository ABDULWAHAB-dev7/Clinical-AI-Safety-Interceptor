"use client";

import { useMemo, useState } from "react";
import { buildSegments } from "@/lib/heatmap";
import { SEVERITY_LABEL } from "@/lib/report";
import type { Severity, ViewSpan } from "@/lib/types";
import { SpanTooltip } from "./SpanTooltip";

// Each severity has a tint, an underline style and a text label, so color is never the only signal.
const STYLE: Record<Severity, string> = {
  CRITICAL: "bg-crit-tint underline decoration-solid decoration-2 decoration-crit underline-offset-4 hover:bg-[#fbe0dc]",
  WARNING: "bg-warn-tint underline decoration-dotted decoration-2 decoration-warn underline-offset-4 hover:bg-[#fbeac6]",
  SAFE: "underline decoration-solid decoration-1 decoration-ok underline-offset-4 hover:bg-ok-tint",
};
const LABEL: Record<Severity, string> = { CRITICAL: "text-crit", WARNING: "text-warn", SAFE: "text-ok" };

/** The note with a highlight per attributed span. Every highlight is a button that can be reached with the keyboard. */
export function NoteViewer({ text, spans }: { text: string; spans: ViewSpan[] }) {
  const { segments, warnings } = useMemo(() => buildSegments(text, spans), [text, spans]);
  const [active, setActive] = useState<number | null>(null);

  return (
    <div className="space-y-5">
      <p className="whitespace-pre-wrap text-lg font-light leading-9 sm:text-xl sm:leading-10" data-testid="note-text">
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
              className={`mx-0.5 inline rounded-sm px-1 py-0.5 text-left align-baseline font-light transition-colors ${STYLE[severity]} ${active === segment.start ? "outline outline-1 outline-ink" : ""}`}
            >
              {segment.text}
              <span className={`ml-1.5 align-middle text-[0.6rem] font-medium uppercase tracking-[0.12em] ${LABEL[severity]}`}>
                {SEVERITY_LABEL[severity]}
              </span>
            </button>
          );
        })}
      </p>

      <div aria-live="polite" className="min-h-6 text-sm text-muted">
        {active === null ? "Select a highlight to see the evidence behind it." : null}
        {segments.map((segment) =>
          segment.severity === null ? null : (
            <SpanTooltip key={segment.start} id={`tip-${segment.start}`} spans={segment.spans} active={active === segment.start} />
          ),
        )}
      </div>

      {warnings.length > 0 ? (
        <p role="alert" className="rounded-xl border border-warn bg-warn-tint p-4 text-sm text-warn">
          {warnings.length} attribution span(s) did not match this text and are not shown:{" "}
          {warnings.map((w) => `${w.spanId} (${w.reason})`).join("; ")}
        </p>
      ) : null}
    </div>
  );
}
