import { shortPatientId } from "@/lib/report";
import { limited } from "@/lib/guard";
import { call, config, NotConfigured, problem } from "@/lib/upstream";

// Reads a saved note and report by id. The only read path the dashboard uses. The browser never holds a certificate
// or a bearer token; both are used here, on the server.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: Request) {
  const over = limited(request, "read");
  if (over) return over;
  let cfg;
  try {
    cfg = config();
  } catch (error) {
    if (error instanceof NotConfigured) return problem(503, "NOT_CONFIGURED", error.message);
    throw error;
  }

  const { searchParams } = new URL(request.url);
  const noteId = searchParams.get("noteId") ?? "";
  const reportId = searchParams.get("reportId") ?? "";
  if (!UUID.test(noteId) || !UUID.test(reportId)) {
    return problem(400, "BAD_REQUEST", "noteId and reportId must both be UUIDs.");
  }

  const note = await call(cfg, "GET", `/v1/interceptor/notes/${noteId}`);
  if (note.status === 0) return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  if (note.status === 404) return problem(404, "NOT_FOUND", "No such note or report.");
  if (note.status === 401 || note.status === 403) {
    return problem(502, "UPSTREAM_REFUSED", "The interceptor refused this dashboard's credentials.");
  }
  if (note.status !== 200) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");

  const report = await call(cfg, "GET", `/v1/interceptor/audit/reports/${reportId}`);
  if (report.status !== 200) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");
  const body = report.body as { note_id: string; patient_pseudo_id: string } & Record<string, unknown>;
  const noteBody = note.body as Record<string, unknown>;
  if (body.note_id !== noteId) return problem(404, "NOT_FOUND", "That report does not belong to that note.");

  // Only what the page needs. The patient id is cut to 8 characters here, so the browser never receives it in full.
  return Response.json(
    {
      note: {
        note_id: noteBody.note_id,
        note_sha256: noteBody.note_sha256,
        char_length: noteBody.char_length,
        pipeline_status: noteBody.pipeline_status,
      },
      report: { ...body, patient_pseudo_id: shortPatientId(body.patient_pseudo_id) },
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
