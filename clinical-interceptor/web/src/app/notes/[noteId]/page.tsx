import { LiveNote } from "@/components/LiveNote";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const metadata = { title: "Note review · Clinical AI Safety Interceptor" };

export default async function NotePage({ params, searchParams }: PageProps<"/notes/[noteId]">) {
  const { noteId } = await params;
  const query = await searchParams;
  const reportId = typeof query.report === "string" ? query.report : "";

  return (
    <div className="space-y-16 pb-8 pt-16 sm:pt-24">
      <header className="max-w-3xl space-y-6">
        <p className="eyebrow">Review</p>
        <h1 className="text-4xl font-extralight leading-tight tracking-tight sm:text-6xl">Note review.</h1>
      </header>
      {UUID.test(noteId) && UUID.test(reportId) ? (
        <LiveNote noteId={noteId} reportId={reportId} />
      ) : (
        <p role="alert" className="text-muted">
          Open this page as <code className="font-mono text-ink">/notes/&lt;note id&gt;?report=&lt;report id&gt;</code>, with both ids
          from the audit response.
        </p>
      )}
    </div>
  );
}
