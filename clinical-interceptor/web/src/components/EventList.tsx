"use client";

import { ArrowDown01Icon, ArrowUp01Icon } from "@hugeicons/core-free-icons";
import { useState } from "react";
import { severityRank } from "@/lib/heatmap";
import { SEVERITY_LABEL } from "@/lib/report";
import type { HallucinationEvent } from "@/lib/types";
import { Icon } from "./Icon";
import { SeverityIcon, TAG } from "./SeverityIcon";

/** The hallucination events as a table, sortable by severity. */
export function EventList({ events }: { events: HallucinationEvent[] }) {
  const [descending, setDescending] = useState(true);
  const rows = [...events].sort((a, b) => {
    const diff = severityRank(a.risk_severity) - severityRank(b.risk_severity);
    return descending ? -diff : diff;
  });

  if (events.length === 0) {
    return <p className="tag tag-ok !rounded-xl !px-4 !py-3 !text-sm">No hallucination events were recorded.</p>;
  }

  return (
    <div className="relative overflow-x-auto" data-lenis-prevent-wheel>
      <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
        <caption className="sr-only">Hallucination events</caption>
        <thead>
          <tr className="border-b border-line-strong text-xs text-muted">
            <th scope="col" className="py-3 pr-4 font-medium">Category</th>
            <th scope="col" className="px-4 py-3" aria-sort={descending ? "descending" : "ascending"}>
              <button type="button" onClick={() => setDescending(!descending)} className="inline-flex items-center gap-1 rounded font-medium text-ink">
                Severity <Icon icon={descending ? ArrowDown01Icon : ArrowUp01Icon} size={14} />
                <span className="sr-only">{descending ? "(highest first)" : "(lowest first)"}</span>
              </button>
            </th>
            <th scope="col" className="px-4 py-3 font-medium">NLI class</th>
            <th scope="col" className="px-4 py-3 font-medium">Confidence</th>
            <th scope="col" className="px-4 py-3 font-medium">Rule</th>
            <th scope="col" className="py-3 pl-4 font-medium">Matched record</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((event) => (
            <tr key={event.event_id} className="border-b border-line transition-colors hover:bg-black/[0.02]">
              <td className="py-3.5 pr-4 font-mono text-xs">{event.category}</td>
              <td className="px-4 py-3.5">
                <span className={TAG[event.risk_severity]}>
                  <SeverityIcon severity={event.risk_severity} />
                  {SEVERITY_LABEL[event.risk_severity]}
                </span>
              </td>
              <td className="px-4 py-3.5 font-mono text-xs">{event.nli_class}</td>
              <td className="px-4 py-3.5 tabular-nums">{event.confidence.toFixed(2)}</td>
              <td className="px-4 py-3.5 font-mono text-xs">{event.risk_rule ?? "none"}</td>
              <td className="py-3.5 pl-4 font-mono text-xs">{event.matched_fhir_ref ?? "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
