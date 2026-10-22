"use client";

import { useEffect, useState } from "react";
import type { AuditReport, NoteIndex } from "@/lib/types";
import { Reveal } from "./Reveal";
import { ReportView } from "./ReportView";
import { ReviewPanel } from "./ReviewPanel";

interface Loaded {
  note: NoteIndex;
  report: AuditReport;
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * The interceptor never stores note text, so the reviewer supplies it. It is checked against the recorded SHA-256
 * before any highlight is drawn, and it lives only in this component's state: never in storage, a URL or a request.
 * The review panel does not need the text, so a finding can be answered before the text is pasted.
 */
export function LiveNote({ noteId, reportId }: { noteId: string; reportId: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [text, setText] = useState<string | null>(null);
  const [mismatch, setMismatch] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/interceptor?noteId=${encodeURIComponent(noteId)}&reportId=${encodeURIComponent(reportId)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body?.error?.message ?? "The report could not be loaded.");
        setLoaded(body as Loaded);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(error instanceof Error ? error.message : "The report could not be loaded.");
      });
    return () => controller.abort();
  }, [noteId, reportId]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!loaded) return;
    const candidate = draft.replace(/\r\n/g, "\n");
    if ((await sha256Hex(candidate)) !== loaded.note.note_sha256) {
      setMismatch("This text does not match the note the interceptor audited, so no highlights are shown.");
      setText(null);
      return;
    }
    setMismatch(null);
    setText(candidate);
  }

  if (failure) {
    return (
      <p role="alert" className="rounded-2xl border border-crit bg-crit-tint p-6 text-ink">
        {failure}
      </p>
    );
  }
  if (!loaded) return <p className="text-muted">Loading report…</p>;
  if (text !== null) return <ReportView text={text} report={loaded.report} reviewable />;

  return (
    <div className="space-y-24">
      <form onSubmit={submit} className="max-w-2xl space-y-5">
        <p className="leading-7 text-muted">
          The report for this note was found ({loaded.report.pipeline_status}). The service does not keep note text. Paste the
          exact text that was audited to see where each finding sits.
        </p>
        <label htmlFor="note-text" className="block text-sm font-medium">
          Note text
        </label>
        <textarea
          id="note-text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={8}
          data-lenis-prevent
          className="field font-mono"
        />
        <button type="submit" className="btn btn-solid">
          Show highlights
        </button>
        {mismatch ? (
          <p role="alert" className="text-sm text-crit">
            {mismatch}
          </p>
        ) : null}
      </form>
      <Reveal>
        <ReviewPanel report={loaded.report} />
      </Reveal>
    </div>
  );
}
