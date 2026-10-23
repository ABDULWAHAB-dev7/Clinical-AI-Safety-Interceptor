"use client";

import { PageHeader } from "@/components/PageHeader";

/** Shown when a page fails while rendering. The message is fixed text: an error's own text could contain note content. */
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="wrap flex flex-col items-center gap-10 pt-12 sm:pt-20">
      <PageHeader eyebrow="Something went wrong" title={<>This page <span className="hl">failed</span>.</>}>
        <p className="lead max-w-md">Nothing was released. Try again, and if it keeps failing, tell the person who runs this dashboard.</p>
      </PageHeader>
      <button type="button" onClick={reset} className="btn btn-solid">
        Try again
      </button>
    </div>
  );
}
