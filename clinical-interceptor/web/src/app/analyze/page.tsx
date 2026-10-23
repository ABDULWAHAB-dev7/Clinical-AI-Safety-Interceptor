import { AnalyzeForm } from "@/components/AnalyzeForm";
import { PATIENTS } from "@/lib/patients";

export const metadata = { title: "Analyse a note · Clinical AI Safety Interceptor" };

// Only the id and the label reach the browser. The bundles and the MRNs stay on the server.
export default function AnalyzePage() {
  const options = PATIENTS.map((p) => ({ id: p.id, label: p.label }));
  return (
    <div className="wrap space-y-16 pt-12 sm:pt-20">
      <header className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
        <p className="eyebrow">Live check</p>
        <h1 className="t-display">
          Analyse a <span className="hl">note</span>.
        </h1>
        <p className="lead max-w-xl">
          The note is checked against the chosen synthetic patient record by the live interceptor. A statement the rules cannot
          settle needs the language model, and until that is configured the note is blocked, not checked.
        </p>
      </header>
      <AnalyzeForm patients={options} />
    </div>
  );
}
