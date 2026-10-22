import { describe, expect, it } from "vitest";
import fixture from "./fixtures/case-0001.json";
import { ACTIONS_FOR, findings, reviewRequest, type ReviewStatus } from "./review";
import type { AuditReport } from "./types";

const report = (fixture as unknown as { report: AuditReport }).report;

describe("findings", () => {
  it("lists every event with its span, critical findings first", () => {
    const items = findings(report, null);
    expect(items).toHaveLength(report.hallucination_events.length);
    expect(items.map((f) => f.severity)).toEqual([...items.map((f) => f.severity)].sort((a, b) => (a === b ? 0 : a === "CRITICAL" ? -1 : 1)));
    for (const item of items) {
      expect(report.attribution_spans.some((s) => s.span_id === item.spanId && s.claim_id === item.claimId)).toBe(true);
      expect(item.resolution).toBe("OPEN");
    }
  });

  it("takes the resolution from the review status", () => {
    const first = report.hallucination_events[0];
    const status: ReviewStatus = {
      report_id: report.report_id,
      pipeline_status: "BLOCKED",
      open_count: 1,
      ready_to_sign: false,
      findings: [{ claim_id: first.claim_id, risk_severity: first.risk_severity, resolution: "CORRECTED", reviewed_at: null }],
    };
    expect(findings(report, status).find((f) => f.claimId === first.claim_id)?.resolution).toBe("CORRECTED");
  });
});

describe("actions", () => {
  it("answers a warning with accept, edit or reject, and a critical finding only with an override", () => {
    expect(ACTIONS_FOR.WARNING).toEqual(["ACCEPT", "EDIT", "REJECT"]);
    expect(ACTIONS_FOR.CRITICAL).toEqual(["OVERRIDE"]);
  });
});

describe("reviewRequest", () => {
  const base = { action: "ACCEPT" as const, reportId: "r", spanId: "s", comment: "Checked the chart", signature: "Dr. Example" };

  it("builds the body from trimmed values", () => {
    const result = reviewRequest({ ...base, comment: "  Checked the chart ", signature: " Dr. Example " });
    expect(result).toEqual({ ok: true, body: { action: "ACCEPT", reportId: "r", spanId: "s", comment: "Checked the chart", signature: "Dr. Example" } });
  });

  it("refuses a missing signature, a missing reason, and over-long entries", () => {
    expect(reviewRequest({ ...base, signature: " a " }).ok).toBe(false);
    expect(reviewRequest({ ...base, comment: "" }).ok).toBe(false);
    expect(reviewRequest({ ...base, comment: "x".repeat(201) }).ok).toBe(false);
  });
});
