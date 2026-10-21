"use client";

import { useState } from "react";
import { severityRank } from "@/lib/heatmap";
import { SEVERITY_ICON, SEVERITY_LABEL } from "@/lib/report";
import type { HallucinationEvent, Severity } from "@/lib/types";

const PILL: Record<Severity, string> = {
  CRITICAL: "bg-red-100 text-red-950 ring-red-300",
  WARNING: "bg-amber-100 text-amber-950 ring-amber-300",
  SAFE: "bg-emerald-100 text-emerald-950 ring-emerald-300",
};

/** The hallucination events as a table, sortable by severity. */
export function EventList({ events }: { events: HallucinationEvent[] }) {
  const [descending, setDescending] = useState(true);
  const rows = [...events].sort((a, b) => {
    const diff = severityRank(a.risk_severity) - severityRank(b.risk_severity);
    return descending ? -diff : diff;
  });

  if (events.length === 0) {
    return (
      <p className="rounded-xl bg-emerald-50 p-4 text-sm text-emerald-950">No hallucination events were recorded.</p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200">
      <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
        <caption className="sr-only">Hallucination events</caption>
        <thead className="bg-slate-50 text-slate-700">
          <tr>
            <th scope="col" className="px-4 py-3 font-semibold">Category</th>
            <th scope="col" className="px-4 py-3" aria-sort={descending ? "descending" : "ascending"}>
              <button
                type="button"
                onClick={() => setDescending(!descending)}
                className="inline-flex items-center gap-1 font-semibold text-slate-900 hover:text-indigo-800"
              >
                Severity <span aria-hidden="true">{descending ? "↓" : "↑"}</span>
                <span className="sr-only">{descending ? "(highest first)" : "(lowest first)"}</span>
              </button>
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">NLI class</th>
            <th scope="col" className="px-4 py-3 font-semibold">Confidence</th>
            <th scope="col" className="px-4 py-3 font-semibold">Rule</th>
            <th scope="col" className="px-4 py-3 font-semibold">Matched record</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((event) => (
            <tr key={event.event_id} className="transition-colors hover:bg-slate-50">
              <td className="px-4 py-3 font-mono text-xs">{event.category}</td>
              <td className="px-4 py-3">
                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${PILL[event.risk_severity]}`}>
                  <span aria-hidden="true">{SEVERITY_ICON[event.risk_severity]}</span>
                  {SEVERITY_LABEL[event.risk_severity]}
                </span>
              </td>
              <td className="px-4 py-3 font-mono text-xs">{event.nli_class}</td>
              <td className="px-4 py-3 tabular-nums">{event.confidence.toFixed(2)}</td>
              <td className="px-4 py-3 font-mono text-xs">{event.risk_rule ?? "none"}</td>
              <td className="px-4 py-3 font-mono text-xs">{event.matched_fhir_ref ?? "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
