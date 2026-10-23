import { LiveNote } from "@/components/LiveNote";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const metadata = { title: "Note review · Clinical AI Safety Interceptor" };

export default async function NotePage({ params, searchParams }: PageProps<"/notes/[noteId]">) {
  const { noteId } = await params;
  const query = await searchParams;
  const reportId = typeof query.report === "string" ? query.report : "";

  return (
    <div className="wrap space-y-16 pt-12 sm:pt-20">
      <header className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
        <p className="eyebrow">Review</p>
        <h1 className="t-display">
          Note <span className="hl">review</span>.
        </h1>
      </header>
      {UUID.test(noteId) && UUID.test(reportId) ? (
        <LiveNote noteId={noteId} reportId={reportId} />
      ) : (
        <p role="alert" className="mx-auto max-w-2xl text-center text-muted">
          Open this page as <code className="font-mono text-ink">/notes/&lt;note id&gt;?report=&lt;report id&gt;</code>, with both ids
          from the audit response.
        </p>
      )}
    </div>
  );
}
