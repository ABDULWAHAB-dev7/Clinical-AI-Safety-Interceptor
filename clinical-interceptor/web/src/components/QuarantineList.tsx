"use client";

import { ArrowRight01Icon, CheckmarkCircle02Icon } from "@hugeicons/core-free-icons";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";

interface Held {
  note_id: string;
  patient: string;
  note_type: string;
  claim_count: number;
  report_id: string | null;
  blocked_at: string;
}

/** Notes held back as blocked and not yet overridden. It shows IDs and counts only, never note text. */
export function QuarantineList() {
  const [notes, setNotes] = useState<Held[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/quarantine", { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body?.error?.message ?? "The quarantine list could not be loaded.");
        setNotes(body.notes as Held[]);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setFailure(error instanceof Error ? error.message : "The quarantine list could not be loaded.");
        }
      });
    return () => controller.abort();
  }, []);

  if (failure) {
    return (
      <p role="status" className="panel max-w-2xl p-6 text-muted">
        {failure}
      </p>
    );
  }
  if (notes === null) return <p className="text-muted">Loading…</p>;
  if (notes.length === 0) {
    return (
      <p className="flex items-center gap-3 border-y border-line py-8 text-muted">
        <span className="text-ok">
          <Icon icon={CheckmarkCircle02Icon} size={22} />
        </span>
        Nothing is being held. Every note has either passed or been overridden by a clinician.
      </p>
    );
  }

  return (
    <div className="relative overflow-x-auto" data-lenis-prevent-wheel>
      <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
        <caption className="sr-only">Notes held for review</caption>
        <thead>
          <tr className="border-b border-line-strong text-xs text-muted">
            <th scope="col" className="py-3 pr-4 font-medium">Note</th>
            <th scope="col" className="px-4 py-3 font-medium">Patient</th>
            <th scope="col" className="px-4 py-3 font-medium">Type</th>
            <th scope="col" className="px-4 py-3 font-medium">Claims</th>
            <th scope="col" className="px-4 py-3 font-medium">Held since</th>
            <th scope="col" className="py-3 pl-4 font-medium"><span className="sr-only">Open</span></th>
          </tr>
        </thead>
        <tbody>
          {notes.map((note) => (
            <tr key={note.note_id} className="border-b border-line">
              <td className="py-4 pr-4 font-mono text-xs">{note.note_id.slice(0, 8)}</td>
              <td className="px-4 py-4 font-mono text-xs">{note.patient}</td>
              <td className="px-4 py-4">{note.note_type}</td>
              <td className="px-4 py-4 tabular-nums">{note.claim_count}</td>
              <td className="px-4 py-4 text-muted">{new Date(note.blocked_at).toLocaleString()}</td>
              <td className="py-4 pl-4 text-right">
                {note.report_id ? (
                  <Link
                    href={`/notes/${note.note_id}?report=${note.report_id}`}
                    className="inline-flex items-center gap-1 rounded-full font-medium underline-offset-4 hover:underline"
                  >
                    Review <Icon icon={ArrowRight01Icon} size={14} />
                  </Link>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
