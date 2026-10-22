import { SEVERITY_LABEL } from "@/lib/report";
import type { ViewSpan } from "@/lib/types";
import { SeverityIcon, TAG } from "./SeverityIcon";

const CLASSIFICATION_LABEL: Record<ViewSpan["classification"], string> = {
  SOURCE_SUPPORTED: "Supported by the record",
  SOURCE_CONTRADICTED: "Contradicted by the record",
  NO_SOURCE_EVIDENCE: "No evidence in the record",
  NON_CLINICAL: "Not a clinical statement",
};

/** The evidence behind one highlight. It never shows patient-identifying text, only codes and resource references. */
export function SpanTooltip({ id, spans, active }: { id: string; spans: ViewSpan[]; active: boolean }) {
  return (
    <div id={id} role="tooltip" hidden={!active} className="panel p-5 text-sm text-ink">
      {spans.map((span) => (
        <section
          key={span.span_id}
          className="space-y-2 [&:not(:first-child)]:mt-5 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line [&:not(:first-child)]:pt-5"
        >
          <p className="flex flex-wrap items-center gap-2">
            <span className={TAG[span.severity]}>
              <SeverityIcon severity={span.severity} />
              {SEVERITY_LABEL[span.severity]}
            </span>
            <span className="text-muted">{CLASSIFICATION_LABEL[span.classification]}</span>
          </p>
          <p className="font-mono text-xs text-muted">{span.classification}</p>
          <p className="leading-6">{span.explanation}</p>
          {span.matched_fhir_ref ? (
            <p className="text-muted">
              Matched record:{" "}
              <code className="rounded bg-black/5 px-1.5 py-0.5 font-mono text-xs text-ink">{span.matched_fhir_ref}</code>
            </p>
          ) : null}
        </section>
      ))}
    </div>
  );
}
