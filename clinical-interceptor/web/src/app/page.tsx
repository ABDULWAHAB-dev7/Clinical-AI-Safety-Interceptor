import { ReportView } from "@/components/ReportView";
import fixture from "@/lib/fixtures/case-0001.json";
import type { CaseData } from "@/lib/types";

const data = fixture as unknown as CaseData;

export default function Home() {
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold">Clinical AI Safety Interceptor</h1>
        <p className="text-slate-700">
          Review of <code className="font-mono">{data.case_id}</code>, a synthetic note checked against a synthetic patient
          record.
        </p>
        <p className="rounded border border-slate-300 bg-slate-50 p-3 text-sm text-slate-800">
          This is saved output, not a live check. Statements that need a language model were scored by a neutral stand-in
          because no model has been approved yet, so they appear as unsupported.
        </p>
      </header>
      <ReportView text={data.note_text} report={data.report} />
    </div>
  );
}
