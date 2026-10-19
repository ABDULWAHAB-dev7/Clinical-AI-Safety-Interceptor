import { SEVERITY_ICON, SEVERITY_LABEL, shortPatientId } from "@/lib/report";
import type { AuditReport, Severity } from "@/lib/types";

const ORDER: Severity[] = ["CRITICAL", "WARNING", "SAFE"];

/** Counts by severity from the report, and the pipeline status as text. */
export function SummaryBar({ report }: { report: AuditReport }) {
  return (
    <section aria-labelledby="summary-heading" className="rounded border border-slate-300 p-4">
      <h2 id="summary-heading" className="text-base font-semibold">
        Summary
      </h2>
      <p className="mt-1 text-lg">
        Pipeline status: <strong>{report.pipeline_status}</strong>
        <span className="text-slate-700"> · overall {SEVERITY_LABEL[report.overall_risk_severity].toLowerCase()}</span>
      </p>
      <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-2">
        {ORDER.map((severity) => (
          <div key={severity}>
            <dt className="text-sm text-slate-700">
              <span aria-hidden="true">{SEVERITY_ICON[severity]} </span>
              {SEVERITY_LABEL[severity]}
            </dt>
            <dd className="text-2xl font-semibold" data-testid={`count-${severity}`}>
              {report.counts[severity] ?? 0}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-slate-700">
        Patient <code className="font-mono">{shortPatientId(report.patient_pseudo_id)}</code> · report{" "}
        <code className="font-mono">{report.report_id.slice(0, 8)}</code>
      </p>
    </section>
  );
}
