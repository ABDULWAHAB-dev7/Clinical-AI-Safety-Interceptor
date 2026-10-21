import { SEVERITY_ICON, SEVERITY_LABEL, shortPatientId } from "@/lib/report";
import type { AuditReport, Severity } from "@/lib/types";

const ORDER: Severity[] = ["CRITICAL", "WARNING", "SAFE"];

const CARD: Record<Severity, string> = {
  CRITICAL: "border-t-red-600 bg-red-50/60",
  WARNING: "border-t-amber-500 bg-amber-50/60",
  SAFE: "border-t-emerald-600 bg-emerald-50/60",
};

const ICON_BG: Record<Severity, string> = {
  CRITICAL: "bg-red-100 text-red-900",
  WARNING: "bg-amber-100 text-amber-900",
  SAFE: "bg-emerald-100 text-emerald-900",
};

/** Counts by severity from the report, and the pipeline status as text. */
export function SummaryBar({ report }: { report: AuditReport }) {
  const blocked = report.pipeline_status === "BLOCKED";
  return (
    <section aria-labelledby="summary-heading" className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="summary-heading" className="text-lg font-semibold">
          Summary
        </h2>
        <p className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm ${blocked ? "bg-red-700 text-white" : "bg-emerald-700 text-white"}`}>
          Pipeline status: <strong>{report.pipeline_status}</strong>
          <span>· overall {SEVERITY_LABEL[report.overall_risk_severity].toLowerCase()}</span>
        </p>
      </div>
      <dl className="grid gap-4 sm:grid-cols-3">
        {ORDER.map((severity) => (
          <div key={severity} className={`rounded-2xl border border-slate-200 border-t-4 p-5 shadow-sm ${CARD[severity]}`}>
            <dt className="flex items-center gap-2.5 text-sm font-medium text-slate-800">
              <span aria-hidden="true" className={`grid size-8 place-items-center rounded-lg text-base ${ICON_BG[severity]}`}>
                {SEVERITY_ICON[severity]}
              </span>
              {SEVERITY_LABEL[severity]}
            </dt>
            <dd className="mt-3 text-4xl font-bold tabular-nums" data-testid={`count-${severity}`}>
              {report.counts[severity] ?? 0}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-sm text-slate-600">
        Patient <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{shortPatientId(report.patient_pseudo_id)}</code>
        {" · "}report <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{report.report_id.slice(0, 8)}</code>
      </p>
    </section>
  );
}
