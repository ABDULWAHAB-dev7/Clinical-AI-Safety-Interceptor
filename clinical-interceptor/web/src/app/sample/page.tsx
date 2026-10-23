import { InformationCircleIcon } from "@hugeicons/core-free-icons";
import { Icon } from "@/components/Icon";
import { ReportView } from "@/components/ReportView";
import fixture from "@/lib/fixtures/case-0001.json";
import type { CaseData } from "@/lib/types";

const data = fixture as unknown as CaseData;

export const metadata = { title: "Sample review · Clinical AI Safety Interceptor" };

export default function Sample() {
  return (
    <div className="wrap space-y-16 pt-12 sm:pt-20">
      <header className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
        <p className="eyebrow">Sample review</p>
        <h1 className="t-display">
          A note, checked <span className="hl">line by line</span>.
        </h1>
        <p className="lead max-w-xl">
          Each highlight shows whether the record supports the statement, contradicts it, or cannot confirm it, with the
          evidence behind the call.
        </p>
        <p className="text-sm text-muted">
          Case <code className="font-mono text-xs text-ink">{data.case_id}</code>, a synthetic note checked against a synthetic
          record.
        </p>
      </header>

      <aside className="mx-auto flex max-w-3xl gap-3 rounded-2xl bg-cream p-6 text-sm leading-6 text-muted">
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
