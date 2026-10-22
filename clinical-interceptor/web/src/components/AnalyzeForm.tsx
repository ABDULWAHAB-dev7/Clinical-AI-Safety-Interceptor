"use client";

import { Alert02Icon, ArrowRight01Icon, Loading03Icon } from "@hugeicons/core-free-icons";
import { useState } from "react";
import { MAX_NOTE_CHARS } from "@/lib/analyze-limits";
import type { CaseData } from "@/lib/types";
import { Icon } from "./Icon";
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
      setResult({
        case_id: "pasted",
        meta: { note_synthetic: true, nli: "see server", verifier_version: "" },
        note_text: body.note_text,
        report: body.report,
      });
    } catch {
      setFailure({ code: "NETWORK", message: "The request failed. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  }

  const remaining = MAX_NOTE_CHARS - text.length;

  return (
    <div className="space-y-24">
      <form onSubmit={submit} className="grid gap-10 lg:grid-cols-[1fr_17rem]">
        <div className="space-y-6">
          <p className="flex items-start gap-3 rounded-xl border border-line-strong p-4 text-sm leading-6 text-muted">
            <span className="mt-0.5 text-warn">
              <Icon icon={Alert02Icon} size={18} />
            </span>
            Paste synthetic notes only. Real patient text must not be entered here.
          </p>

          <div className="space-y-2">
            <label htmlFor="note" className="text-sm font-medium">
              Note text
            </label>
            <textarea
              id="note"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={MAX_NOTE_CHARS}
              rows={12}
              data-lenis-prevent
              placeholder="Paste a synthetic clinical note here…"
              className="field font-mono !leading-6"
            />
            <p className={`text-right text-xs tabular-nums ${remaining < 200 ? "font-semibold text-warn" : "text-muted"}`}>
              {text.length} / {MAX_NOTE_CHARS} characters
            </p>
          </div>
        </div>

        <div className="space-y-8 lg:pt-[4.25rem]">
          <div className="space-y-2">
            <label htmlFor="patient" className="text-sm font-medium">
              Synthetic patient
            </label>
            <select id="patient" value={patient} onChange={(event) => setPatient(event.target.value)} className="field">
              {patients.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <p className="text-xs leading-5 text-muted">The record the note is checked against.</p>
          </div>

          <button type="submit" disabled={busy || text.trim().length === 0} className="btn btn-solid w-full">
            {busy ? (
              <>
                <Icon icon={Loading03Icon} size={16} /> Analysing…
              </>
            ) : (
              <>
                Analyse note <Icon icon={ArrowRight01Icon} size={16} />
              </>
            )}
          </button>
        </div>
      </form>

      {failure ? (
        <div role="alert" className="rounded-2xl border border-crit bg-crit-tint p-6 text-crit">
          <p className="font-medium">Not analysed</p>
          <p className="mt-1 text-ink">{failure.message}</p>
          <p className="mt-3 font-mono text-xs">{failure.code}</p>
        </div>
      ) : null}
      {note ? <p className="panel p-6 text-muted">{note}</p> : null}
      {result ? <ReportView text={result.note_text} report={result.report} reviewable /> : null}
    </div>
  );
}
