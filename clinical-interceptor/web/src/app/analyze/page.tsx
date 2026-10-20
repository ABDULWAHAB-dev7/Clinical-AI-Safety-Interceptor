import { AnalyzeForm } from "@/components/AnalyzeForm";
import { PATIENTS } from "@/lib/patients";

// Only the id and the label reach the browser. The bundles and the MRNs stay on the server.
export default function AnalyzePage() {
  const options = PATIENTS.map((p) => ({ id: p.id, label: p.label }));
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold">Analyse a note</h1>
        <p className="text-slate-700">
          The note is checked against the chosen synthetic patient record by the live interceptor. A statement the rules
          cannot settle needs the language model, and until that is configured the note is blocked, not checked.
        </p>
      </header>
      <AnalyzeForm patients={options} />
    </div>
  );
}
