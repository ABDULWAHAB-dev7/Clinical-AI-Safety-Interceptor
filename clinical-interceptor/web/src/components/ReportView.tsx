import { viewSpans } from "@/lib/report";
import type { AuditReport } from "@/lib/types";
import { EventList } from "./EventList";
import { NoteViewer } from "./NoteViewer";
import { Reveal } from "./Reveal";
import { ReviewPanel } from "./ReviewPanel";
import { SummaryBar } from "./SummaryBar";

/** `reviewable` shows the clinician review panel. The saved sample is not a live report, so it has none. */
export function ReportView({ text, report, reviewable = false }: { text: string; report: AuditReport; reviewable?: boolean }) {
  return (
    <div className="space-y-20">
      <SummaryBar report={report} />
      <Reveal as="section" className="space-y-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="note-heading" className="text-2xl font-light tracking-tight sm:text-3xl">
            Note
          </h2>
          <p className="text-sm text-muted">Select a highlight to see its evidence</p>
        </div>
        <NoteViewer text={text} spans={viewSpans(report)} />
      </Reveal>
      <Reveal as="section" className="space-y-6">
        <h2 id="events-heading" className="text-2xl font-light tracking-tight sm:text-3xl">
          Events
        </h2>
        <EventList events={report.hallucination_events} />
      </Reveal>
      {reviewable ? (
        <Reveal>
          <ReviewPanel report={report} />
        </Reveal>
      ) : null}
    </div>
  );
}
