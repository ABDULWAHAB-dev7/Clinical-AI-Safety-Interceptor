import { viewSpans } from "@/lib/report";
import type { AuditReport } from "@/lib/types";
import { EventList } from "./EventList";
import { NoteViewer } from "./NoteViewer";
import { SummaryBar } from "./SummaryBar";

export function ReportView({ text, report }: { text: string; report: AuditReport }) {
  return (
    <div className="space-y-8">
      <SummaryBar report={report} />
      <section aria-labelledby="note-heading" className="space-y-5 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="note-heading" className="text-lg font-semibold">
            Note
          </h2>
          <p className="text-sm text-slate-600">Select a highlight to see its evidence</p>
        </div>
        <NoteViewer text={text} spans={viewSpans(report)} />
      </section>
      <section aria-labelledby="events-heading" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <h2 id="events-heading" className="text-lg font-semibold">
          Events
        </h2>
        <EventList events={report.hallucination_events} />
      </section>
    </div>
  );
}
