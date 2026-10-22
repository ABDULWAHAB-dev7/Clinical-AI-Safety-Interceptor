import type { AuditReport, Category, Severity, ViewSpan } from "./types";

/** Joins each attribution span with its event by claim_id. A span with no event is supported (SAFE). */
export function viewSpans(report: AuditReport): ViewSpan[] {
  const byClaim = new Map(report.hallucination_events.map((event) => [event.claim_id, event]));
  return report.attribution_spans.map((span) => {
    const event = span.claim_id ? byClaim.get(span.claim_id) : undefined;
    const severity: Severity = event ? event.risk_severity : "SAFE";
    const category: Category | null = event ? event.category : null;
    return { ...span, severity, category, matched_fhir_ref: event?.matched_fhir_ref ?? matchedRef(span.explanation) };
  });
}

/** The explanation texts name the FHIR resource they matched, for example "AllergyIntolerance/allergy-001". */
export function matchedRef(explanation: string): string | null {
  const found = explanation.match(/\b[A-Z][A-Za-z]+\/[A-Za-z0-9._-]+/);
  return found ? found[0] : null;
}

/** Patient pseudo-IDs are shown as their first 8 characters only. */
export function shortPatientId(pseudoId: string): string {
  return pseudoId.replace(/^pid_/, "").slice(0, 8);
}

export const SEVERITY_LABEL: Record<Severity, string> = { CRITICAL: "Critical", WARNING: "Warning", SAFE: "Supported" };
