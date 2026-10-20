"use client";

import { useState } from "react";
import { MAX_NOTE_CHARS } from "@/lib/analyze-limits";
import type { CaseData } from "@/lib/types";
import { ReportView } from "./ReportView";

interface Option {
  id: string;
  label: string;
}

interface Failure {
  code: string;
  message: string;
}

/**
 * Paste a note, pick a synthetic patient, and see the report. The text lives only in component state: it is not
 * stored in browser storage, the URL, or the interceptor. Paste synthetic notes only.
 */
export function AnalyzeForm({ patients }: { patients: Option[] }) {
  const [text, setText] = useState("");
  const [patient, setPatient] = useState(patients[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [result, setResult] = useState<CaseData | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    setResult(null);
    setNote(null);
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, patient }),
      });
      const body = await response.json();
      if (!response.ok) {
        setFailure({ code: body?.error?.code ?? "ERROR", message: body?.error?.message ?? "The note was not analysed." });
        return;
      }
      if (!body.report) {
        setNote(body.message);
        return;
      }
      setResult({ case_id: "pasted", meta: { note_synthetic: true, nli: "see server", verifier_version: "" },
        note_text: body.note_text, report: body.report });
    } catch {
      setFailure({ code: "NETWORK", message: "The request failed. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="space-y-4">
        <p className="rounded border border-amber-700 bg-amber-50 p-3 text-sm text-amber-950">
          Paste synthetic notes only. Real patient text must not be entered here.
        </p>
        <label htmlFor="note" className="block font-semibold">
          Note text
        </label>
        <textarea
          id="note"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={MAX_NOTE_CHARS}
          rows={8}
          className="w-full rounded border border-slate-500 p-2 font-mono text-sm"
        />
        <p className="text-sm text-slate-700">
          {text.length} / {MAX_NOTE_CHARS} characters
        </p>
        <label htmlFor="patient" className="block font-semibold">
          Synthetic patient
        </label>
        <select
          id="patient"
          value={patient}
          onChange={(event) => setPatient(event.target.value)}
          className="w-full rounded border border-slate-500 p-2"
        >
          {patients.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={busy || text.trim().length === 0}
          className="rounded bg-slate-900 px-4 py-2 font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Analysing…" : "Analyse note"}
        </button>
      </form>

      {failure ? (
        <div role="alert" className="rounded border border-red-700 bg-red-50 p-3 text-red-950">
          <p className="font-semibold">Not analysed</p>
          <p>{failure.message}</p>
          <p className="font-mono text-xs">{failure.code}</p>
        </div>
      ) : null}
      {note ? <p className="text-slate-800">{note}</p> : null}
      {result ? <ReportView text={result.note_text} report={result.report} /> : null}
    </div>
  );
}
