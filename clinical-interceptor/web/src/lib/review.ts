import type { AuditReport, Severity, ViewSpan } from "./types";
import { viewSpans } from "./report";

// What a clinician can do about one finding. A warning is answered with accept, edit or reject. A critical finding needs
// the documented override. The server maps these to the interceptor's acknowledge and override endpoints.

export type ReviewAction = "ACCEPT" | "EDIT" | "REJECT" | "OVERRIDE";
export type Resolution = "OPEN" | "FALSE_POSITIVE" | "CORRECTED" | "CONFIRMED_ERROR";

export const ACTIONS_FOR: Record<Exclude<Severity, "SAFE">, ReviewAction[]> = {
  WARNING: ["ACCEPT", "EDIT", "REJECT"],
  CRITICAL: ["OVERRIDE"],
};

export const ACTION_LABEL: Record<ReviewAction, string> = {
  ACCEPT: "Accept as written",
  EDIT: "Mark as edited",
  REJECT: "Reject the statement",
  OVERRIDE: "Override with a reason",
};

export const RESOLUTION_LABEL: Record<Resolution, string> = {
  OPEN: "Waiting for review",
  FALSE_POSITIVE: "Accepted",
  CORRECTED: "Edited",
  CONFIRMED_ERROR: "Rejected",
};

export interface Finding {
  claimId: string;
  spanId: string;
  severity: Exclude<Severity, "SAFE">;
  category: string;
  resolution: Resolution;
}

export interface ReviewStatus {
  report_id: string;
  pipeline_status: string;
  open_count: number;
  ready_to_sign: boolean;
  findings: { claim_id: string; risk_severity: Severity; resolution: Resolution; reviewed_at: string | null }[];
}

/** Joins the report's events with their spans (a review is made on a span) and with the review status. */
export function findings(report: AuditReport, status: ReviewStatus | null): Finding[] {
  const spans: ViewSpan[] = viewSpans(report);
  const spanByClaim = new Map(spans.filter((s) => s.claim_id).map((s) => [s.claim_id as string, s]));
  const resolution = new Map((status?.findings ?? []).map((f) => [f.claim_id, f.resolution]));
  const out: Finding[] = [];
  for (const event of report.hallucination_events) {
    const span = spanByClaim.get(event.claim_id);
    if (!span) continue;
    out.push({
      claimId: event.claim_id,
      spanId: span.span_id,
      severity: event.risk_severity,
      category: event.category,
      resolution: resolution.get(event.claim_id) ?? "OPEN",
    });
  }
  return out.sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "CRITICAL" ? -1 : 1));
}

/** The request body the review route sends on, or an error message. Nothing is sent without a reason and a signature. */
export function reviewRequest(input: {
  action: ReviewAction;
  reportId: string;
  spanId: string;
  comment: string;
  signature: string;
}): { ok: true; body: Record<string, string> } | { ok: false; message: string } {
  const comment = input.comment.trim();
  const signature = input.signature.trim();
  if (signature.length < 3) return { ok: false, message: "Enter your name or signature (at least 3 characters)." };
  if (comment.length < 3) return { ok: false, message: "Give a short reason (at least 3 characters)." };
  if (comment.length > 200 || signature.length > 200) return { ok: false, message: "Keep each entry under 200 characters." };
  return { ok: true, body: { action: input.action, reportId: input.reportId, spanId: input.spanId, comment, signature } };
}
