import { ReportView } from "@/components/ReportView";
import fixture from "@/lib/fixtures/case-0001.json";
import type { CaseData } from "@/lib/types";

const data = fixture as unknown as CaseData;

export default function Home() {
  return (
    <div className="space-y-10">
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-indigo-600 via-indigo-700 to-slate-900 p-8 text-white shadow-xl sm:p-12">
        <div aria-hidden="true" className="pointer-events-none absolute -right-16 -top-16 size-64 rounded-full bg-white/10 blur-2xl" />
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-200">Sample review</p>
        <h1 className="mt-3 max-w-3xl text-3xl font-bold leading-tight sm:text-4xl">
          Every statement in a clinical note, checked against the patient record
        </h1>
        <p className="mt-4 max-w-2xl text-indigo-50 sm:text-lg">
          Each highlight shows whether the record supports the statement, contradicts it, or cannot confirm it, with the
          evidence behind the call.
        </p>
        <p className="mt-6 text-sm text-indigo-100">
          Case <code className="rounded bg-white/15 px-1.5 py-0.5 font-mono text-white">{data.case_id}</code>, a synthetic
          note checked against a synthetic record.
        </p>
      </section>

      <aside className="flex gap-3 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950 shadow-sm">
        <span aria-hidden="true" className="mt-0.5 text-base">ℹ</span>
        <p>
          This is saved output, not a live check. Statements that need a language model were scored by a neutral stand-in
          in this saved output, so they appear as unsupported.
        </p>
      </aside>

      <ReportView text={data.note_text} report={data.report} />
    </div>
  );
}
