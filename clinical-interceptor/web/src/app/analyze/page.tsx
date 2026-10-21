import { AnalyzeForm } from "@/components/AnalyzeForm";
import { PATIENTS } from "@/lib/patients";

// Only the id and the label reach the browser. The bundles and the MRNs stay on the server.
export default function AnalyzePage() {
  const options = PATIENTS.map((p) => ({ id: p.id, label: p.label }));
  return (
    <div className="space-y-8">
      <header className="max-w-3xl space-y-3">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Live check</p>
        <h1 className="text-3xl font-bold leading-tight">Analyse a note</h1>
        <p className="text-base leading-7 text-slate-700">
          The note is checked against the chosen synthetic patient record by the live interceptor. A statement the rules
          cannot settle needs the language model, and until that is configured the note is blocked, not checked.
        </p>
      </header>
      <AnalyzeForm patients={options} />
    </div>
  );
}
