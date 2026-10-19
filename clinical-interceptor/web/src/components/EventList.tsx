"use client";

import { useState } from "react";
import { severityRank } from "@/lib/heatmap";
import { SEVERITY_ICON, SEVERITY_LABEL } from "@/lib/report";
import type { HallucinationEvent } from "@/lib/types";

/** The hallucination events as a table, sortable by severity. */
export function EventList({ events }: { events: HallucinationEvent[] }) {
  const [descending, setDescending] = useState(true);
  const rows = [...events].sort((a, b) => {
    const diff = severityRank(a.risk_severity) - severityRank(b.risk_severity);
    return descending ? -diff : diff;
  });

  if (events.length === 0) return <p>No hallucination events were recorded.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
        <caption className="sr-only">Hallucination events</caption>
        <thead>
          <tr className="border-b border-slate-400">
            <th scope="col" className="p-2">Category</th>
            <th scope="col" className="p-2" aria-sort={descending ? "descending" : "ascending"}>
              <button type="button" onClick={() => setDescending(!descending)} className="font-semibold underline">
                Severity {descending ? "(highest first)" : "(lowest first)"}
              </button>
            </th>
            <th scope="col" className="p-2">NLI class</th>
            <th scope="col" className="p-2">Confidence</th>
            <th scope="col" className="p-2">Rule</th>
            <th scope="col" className="p-2">Matched record</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((event) => (
            <tr key={event.event_id} className="border-b border-slate-200">
              <td className="p-2 font-mono text-xs">{event.category}</td>
              <td className="p-2">
                <span aria-hidden="true">{SEVERITY_ICON[event.risk_severity]} </span>
                {SEVERITY_LABEL[event.risk_severity]}
              </td>
              <td className="p-2 font-mono text-xs">{event.nli_class}</td>
              <td className="p-2">{event.confidence.toFixed(2)}</td>
              <td className="p-2 font-mono text-xs">{event.risk_rule ?? "none"}</td>
              <td className="p-2 font-mono text-xs">{event.matched_fhir_ref ?? "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
