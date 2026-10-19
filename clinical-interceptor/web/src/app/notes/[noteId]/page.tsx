import { LiveNote } from "@/components/LiveNote";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function NotePage({ params, searchParams }: PageProps<"/notes/[noteId]">) {
  const { noteId } = await params;
  const query = await searchParams;
  const reportId = typeof query.report === "string" ? query.report : "";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Note review</h1>
      {UUID.test(noteId) && UUID.test(reportId) ? (
        <LiveNote noteId={noteId} reportId={reportId} />
      ) : (
        <p role="alert">
          Open this page as <code className="font-mono">/notes/&lt;note id&gt;?report=&lt;report id&gt;</code>, with both ids
          from the audit response.
        </p>
      )}
    </div>
  );
}
