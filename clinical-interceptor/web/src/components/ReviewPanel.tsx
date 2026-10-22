"use client";

import { CheckmarkCircle02Icon, Loading03Icon } from "@hugeicons/core-free-icons";
import { useCallback, useEffect, useState } from "react";
import {
  ACTION_LABEL,
  ACTIONS_FOR,
  findings as joinFindings,
  RESOLUTION_LABEL,
  reviewRequest,
  type Finding,
  type ReviewAction,
  type ReviewStatus,
} from "@/lib/review";
import { SEVERITY_LABEL } from "@/lib/report";
import type { AuditReport } from "@/lib/types";
import { Icon } from "./Icon";
import { SeverityIcon, TAG } from "./SeverityIcon";

async function fetchStatus(reportId: string): Promise<{ status: ReviewStatus | null; error: string | null }> {
  try {
    const response = await fetch(`/api/review-status?reportId=${encodeURIComponent(reportId)}`);
    const body = await response.json();
    if (!response.ok) return { status: null, error: body?.error?.message ?? "Review status is not available." };
    return { status: body as ReviewStatus, error: null };
  } catch {
    return { status: null, error: "Review status is not available." };
  }
}

/**
 * The clinician's answers to the findings in a report (PRD journey B and C). A warning is accepted, edited or rejected,
 * a critical finding is overridden with a reason. Each answer is recorded once and never edited. The name is
 * self-asserted: it is turned into a pseudonym by the interceptor, it is not a verified identity.
 */
export function ReviewPanel({ report }: { report: AuditReport }) {
  const [status, setStatus] = useState<ReviewStatus | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [signature, setSignature] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const apply = useCallback((result: Awaited<ReturnType<typeof fetchStatus>>) => {
    setUnavailable(result.error);
    if (result.status) setStatus(result.status);
  }, []);

  useEffect(() => {
    let current = true;
    fetchStatus(report.report_id).then((result) => {
      if (current) apply(result);
    });
    return () => {
      current = false;
    };
  }, [report.report_id, apply]);

  const items = joinFindings(report, status);
  if (items.length === 0) return null;

  async function answer(finding: Finding, action: ReviewAction) {
    const request = reviewRequest({
      action,
      reportId: report.report_id,
      spanId: finding.spanId,
      comment: reasons[finding.claimId] ?? "",
      signature,
    });
    if (!request.ok) {
      setMessage(request.message);
      return;
    }
    setBusy(`${finding.claimId}:${action}`);
    setMessage(null);
    try {
      const response = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request.body),
      });
      const body = await response.json();
      if (!response.ok) setMessage(body?.error?.message ?? "The answer was not recorded.");
      else apply(await fetchStatus(report.report_id));
    } catch {
      setMessage("The answer was not recorded. Check your connection and try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="review-heading" className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <p className="eyebrow">Clinician review</p>
          <h2 id="review-heading" className="text-2xl font-light tracking-tight sm:text-3xl">
            Answer each finding
          </h2>
        </div>
        {status ? (
          <p className={`tag ${status.ready_to_sign ? "tag-ok" : "tag-neutral"} !px-3.5 !py-1.5 !text-sm`} data-testid="review-ready">
            {status.ready_to_sign ? "Ready to sign" : `${status.open_count} waiting · not ready to sign`}
          </p>
        ) : null}
      </div>

      {unavailable ? (
        <p role="status" className="panel p-5 text-sm text-muted">
          {unavailable}
        </p>
      ) : (
        <>
          <div className="max-w-sm space-y-2">
            <label htmlFor="signature" className="text-sm font-medium">
              Your name
            </label>
            <input
              id="signature"
              value={signature}
              onChange={(event) => setSignature(event.target.value)}
              maxLength={200}
              autoComplete="off"
              className="field"
              placeholder="Dr. Example"
            />
            <p className="text-xs leading-5 text-muted">Kept only as a pseudonym. It is typed, not verified.</p>
          </div>

          <ul className="divide-y divide-line border-y border-line">
            {items.map((finding) => {
              const open = finding.resolution === "OPEN";
              const reasonId = `reason-${finding.claimId}`;
              return (
                <li key={finding.claimId} className="grid gap-4 py-6 md:grid-cols-[14rem_1fr]">
                  <div className="space-y-2">
                    <span className={TAG[finding.severity]}>
                      <SeverityIcon severity={finding.severity} />
                      {SEVERITY_LABEL[finding.severity]}
                    </span>
                    <p className="font-mono text-xs text-muted">{finding.category}</p>
                  </div>
                  {open ? (
                    <div className="space-y-3">
                      <label htmlFor={reasonId} className="text-sm font-medium">
                        {finding.severity === "CRITICAL" ? "Reason for the override" : "Reason"}
                      </label>
                      <input
                        id={reasonId}
                        value={reasons[finding.claimId] ?? ""}
                        onChange={(event) => setReasons({ ...reasons, [finding.claimId]: event.target.value })}
                        maxLength={200}
                        autoComplete="off"
                        className="field"
                      />
                      <div className="flex flex-wrap gap-2">
                        {ACTIONS_FOR[finding.severity].map((action) => (
                          <button
                            key={action}
                            type="button"
                            disabled={busy !== null}
                            onClick={() => answer(finding, action)}
                            className={`btn ${action === "OVERRIDE" || action === "ACCEPT" ? "btn-solid" : "btn-line"} !py-2`}
                          >
                            {busy === `${finding.claimId}:${action}` ? <Icon icon={Loading03Icon} size={16} /> : null}
                            {ACTION_LABEL[action]}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="flex items-center gap-2 text-sm">
                      <span className="text-ok">
                        <Icon icon={CheckmarkCircle02Icon} size={18} />
                      </span>
                      {RESOLUTION_LABEL[finding.resolution]}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {message ? (
            <p role="alert" className="text-sm text-crit">
              {message}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
