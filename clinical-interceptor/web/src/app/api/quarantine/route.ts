import { limited } from "@/lib/guard";
import { shortPatientId } from "@/lib/report";
import { call, config, NotConfigured, problem } from "@/lib/upstream";

// The notes held back as blocked and not yet overridden. IDs and counts only, never note text. The patient id is cut to
// 8 characters here, so the browser never receives it in full.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface QuarantineNote {
  note_id: string;
  patient_pseudo_id: string;
  note_type: string;
  claim_count: number;
  report_id: string | null;
  blocked_at: string;
}

export async function GET(request: Request) {
  const over = limited(request, "read");
  if (over) return over;
  let cfg;
  try {
    cfg = config(["interceptor:read"]);
  } catch (error) {
    if (error instanceof NotConfigured) return problem(503, "NOT_CONFIGURED", error.message);
    throw error;
  }
  const result = await call(cfg, "GET", "/v1/interceptor/quarantine?limit=50");
  if (result.status === 0) return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  if (result.status === 401 || result.status === 403) {
    return problem(502, "UPSTREAM_REFUSED", "The interceptor refused this dashboard's credentials.");
  }
  if (result.status !== 200) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");
  const body = result.body as { notes: QuarantineNote[] };
  return Response.json(
    {
      notes: body.notes.map((n) => ({
        note_id: n.note_id,
        patient: shortPatientId(n.patient_pseudo_id),
        note_type: n.note_type,
        claim_count: n.claim_count,
        report_id: n.report_id,
        blocked_at: n.blocked_at,
      })),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
