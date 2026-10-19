import { viewSpans } from "@/lib/report";
import type { AuditReport } from "@/lib/types";
import { EventList } from "./EventList";
import { NoteViewer } from "./NoteViewer";
import { SummaryBar } from "./SummaryBar";

export function ReportView({ text, report }: { text: string; report: AuditReport }) {
  return (
    <div className="space-y-8">
      <SummaryBar report={report} />
      <section aria-labelledby="note-heading" className="space-y-3">
        <h2 id="note-heading" className="text-base font-semibold">
          Note
        </h2>
        <NoteViewer text={text} spans={viewSpans(report)} />
      </section>
      <section aria-labelledby="events-heading" className="space-y-3">
        <h2 id="events-heading" className="text-base font-semibold">
          Events
        </h2>
        <EventList events={report.hallucination_events} />
      </section>
    </div>
  );
}
