import { InformationCircleIcon } from "@hugeicons/core-free-icons";
import { Icon } from "@/components/Icon";
import { ReportView } from "@/components/ReportView";
import fixture from "@/lib/fixtures/case-0001.json";
import type { CaseData } from "@/lib/types";

const data = fixture as unknown as CaseData;

export const metadata = { title: "Sample review · Clinical AI Safety Interceptor" };

export default function Sample() {
  return (
    <div className="space-y-16 pb-8 pt-16 sm:pt-24">
      <header className="max-w-3xl space-y-6">
        <p className="eyebrow">Sample review</p>
        <h1 className="text-4xl font-extralight leading-tight tracking-tight sm:text-6xl">
          A note, checked line by line.
        </h1>
        <p className="max-w-xl text-lg font-light leading-8 text-muted">
          Each highlight shows whether the record supports the statement, contradicts it, or cannot confirm it, with the
          evidence behind the call.
        </p>
        <p className="text-sm text-muted">
          Case <code className="font-mono text-xs text-ink">{data.case_id}</code>, a synthetic note checked against a synthetic
          record.
        </p>
      </header>

      <aside className="panel flex max-w-3xl gap-3 p-5 text-sm leading-6 text-muted">
        <span className="mt-0.5 text-ink">
          <Icon icon={InformationCircleIcon} size={18} />
        </span>
        <p>
          This is saved output, not a live check. Statements that need a language model were scored by a neutral stand-in in
          this saved output, so they appear as unsupported.
        </p>
      </aside>

      <ReportView text={data.note_text} report={data.report} />
    </div>
  );
}
