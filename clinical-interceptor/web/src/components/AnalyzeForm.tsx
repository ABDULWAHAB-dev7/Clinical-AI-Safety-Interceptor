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

  const remaining = MAX_NOTE_CHARS - text.length;

  return (
    <div className="space-y-10">
      <form onSubmit={submit} className="grid gap-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-5">
          <p className="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
            <span aria-hidden="true">⚠</span>
            Paste synthetic notes only. Real patient text must not be entered here.
          </p>

          <div className="space-y-2">
            <label htmlFor="note" className="block text-sm font-semibold text-slate-900">
              Note text
            </label>
            <textarea
              id="note"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={MAX_NOTE_CHARS}
              rows={12}
              placeholder="Paste a synthetic clinical note here…"
              className="w-full rounded-xl border border-slate-300 bg-slate-50 p-4 font-mono text-sm leading-6 text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:bg-white"
            />
            <p className={`text-right text-xs tabular-nums ${remaining < 200 ? "font-semibold text-amber-900" : "text-slate-600"}`}>
              {text.length} / {MAX_NOTE_CHARS} characters
            </p>
          </div>
        </div>

        <div className="space-y-5">
          <div className="space-y-2">
            <label htmlFor="patient" className="block text-sm font-semibold text-slate-900">
              Synthetic patient
            </label>
            <select
              id="patient"
              value={patient}
              onChange={(event) => setPatient(event.target.value)}
              className="w-full rounded-xl border border-slate-300 bg-white p-3 text-sm text-slate-900 focus:border-indigo-500"
            >
              {patients.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <p className="text-xs leading-5 text-slate-600">The record the note is checked against.</p>
          </div>

          <button
            type="submit"
            disabled={busy || text.trim().length === 0}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
          >
            {busy ? "Analysing…" : "Analyse note"}
          </button>
        </div>
      </form>

      {failure ? (
        <div role="alert" className="rounded-2xl border border-red-300 bg-red-50 p-5 text-red-950 shadow-sm">
          <p className="font-semibold">Not analysed</p>
          <p className="mt-1">{failure.message}</p>
          <p className="mt-2 font-mono text-xs text-red-900">{failure.code}</p>
        </div>
      ) : null}
      {note ? <p className="rounded-2xl border border-slate-200 bg-white p-5 text-slate-800 shadow-sm">{note}</p> : null}
      {result ? <ReportView text={result.note_text} report={result.report} /> : null}
    </div>
  );
}
