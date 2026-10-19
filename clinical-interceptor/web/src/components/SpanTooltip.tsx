import { SEVERITY_ICON, SEVERITY_LABEL } from "@/lib/report";
import type { ViewSpan } from "@/lib/types";

const CLASSIFICATION_LABEL: Record<ViewSpan["classification"], string> = {
  SOURCE_SUPPORTED: "Supported by the record",
  SOURCE_CONTRADICTED: "Contradicted by the record",
  NO_SOURCE_EVIDENCE: "No evidence in the record",
  NON_CLINICAL: "Not a clinical statement",
};

/** The evidence behind one highlight. It never shows patient-identifying text, only codes and resource references. */
export function SpanTooltip({ id, spans, active }: { id: string; spans: ViewSpan[]; active: boolean }) {
  return (
    <div id={id} role="tooltip" hidden={!active} className="rounded border border-slate-300 bg-white p-3 text-sm">
      {spans.map((span) => (
        <section key={span.span_id} className="space-y-1 [&:not(:first-child)]:mt-3 [&:not(:first-child)]:border-t [&:not(:first-child)]:pt-3">
          <p className="font-semibold">
            <span aria-hidden="true">{SEVERITY_ICON[span.severity]} </span>
            {SEVERITY_LABEL[span.severity]}
            <span className="font-normal text-slate-700"> · {CLASSIFICATION_LABEL[span.classification]}</span>
          </p>
          <p className="font-mono text-xs text-slate-700">{span.classification}</p>
          <p>{span.explanation}</p>
          {span.matched_fhir_ref ? (
            <p className="text-slate-700">
              Matched record: <code className="font-mono">{span.matched_fhir_ref}</code>
            </p>
          ) : null}
        </section>
      ))}
    </div>
  );
}
