import { SEVERITY_LABEL, shortPatientId } from "@/lib/report";
import type { AuditReport, Severity } from "@/lib/types";
import { SeverityIcon } from "./SeverityIcon";

const ORDER: Severity[] = ["CRITICAL", "WARNING", "SAFE"];
const NUMBER: Record<Severity, string> = { CRITICAL: "text-crit", WARNING: "text-warn", SAFE: "text-ok" };

/** Counts by severity from the report, and the pipeline status as text. */
export function SummaryBar({ report }: { report: AuditReport }) {
  const blocked = report.pipeline_status === "BLOCKED";
  return (
    <section aria-labelledby="summary-heading" className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <p className="eyebrow">Result</p>
          <h2 id="summary-heading" className="text-3xl font-light tracking-tight sm:text-4xl">
            {blocked ? "Held back for review" : "Cleared by the record"}
          </h2>
        </div>
        <p className={`tag ${blocked ? "tag-crit" : "tag-ok"} !px-3.5 !py-1.5 !text-sm`}>
          Pipeline status: <strong className="font-semibold">{report.pipeline_status}</strong>
          <span className="font-normal">· overall {SEVERITY_LABEL[report.overall_risk_severity].toLowerCase()}</span>
        </p>
      </div>

      <dl className="grid grid-cols-3 divide-x divide-line border-y border-line">
        {ORDER.map((severity) => (
          <div key={severity} className="px-2 py-6 sm:px-6 sm:first:pl-0">
            <dt className="flex items-center gap-2 text-xs text-muted sm:text-sm">
              <span className={NUMBER[severity]}>
                <SeverityIcon severity={severity} size={16} />
              </span>
              {SEVERITY_LABEL[severity]}
            </dt>
            <dd className={`mt-3 text-5xl font-extralight tabular-nums sm:text-6xl ${NUMBER[severity]}`} data-testid={`count-${severity}`}>
              {report.counts[severity] ?? 0}
            </dd>
          </div>
        ))}
      </dl>

      <p className="text-sm text-muted">
        Patient <code className="font-mono text-xs text-ink">{shortPatientId(report.patient_pseudo_id)}</code>
        {" · "}report <code className="font-mono text-xs text-ink">{report.report_id.slice(0, 8)}</code>
      </p>
    </section>
  );
}
