import { AnalyzeForm } from "@/components/AnalyzeForm";
import { PATIENTS } from "@/lib/patients";

export const metadata = { title: "Analyse a note · Clinical AI Safety Interceptor" };

// Only the id and the label reach the browser. The bundles and the MRNs stay on the server.
export default function AnalyzePage() {
  const options = PATIENTS.map((p) => ({ id: p.id, label: p.label }));
  return (
    <div className="space-y-16 pb-8 pt-16 sm:pt-24">
      <header className="max-w-3xl space-y-6">
        <p className="eyebrow">Live check</p>
        <h1 className="text-4xl font-extralight leading-tight tracking-tight sm:text-6xl">Analyse a note.</h1>
        <p className="max-w-xl text-lg font-light leading-8 text-muted">
          The note is checked against the chosen synthetic patient record by the live interceptor. A statement the rules cannot
          settle needs the language model, and until that is configured the note is blocked, not checked.
        </p>
      </header>
      <AnalyzeForm patients={options} />
    </div>
  );
}
