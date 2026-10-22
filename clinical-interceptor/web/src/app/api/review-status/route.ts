import { call, config, NotConfigured, problem } from "@/lib/upstream";

// Which findings of a report still wait for a clinician, and whether the note is ready to sign.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: Request) {
  const reportId = new URL(request.url).searchParams.get("reportId") ?? "";
  if (!UUID.test(reportId)) return problem(400, "BAD_REQUEST", "reportId must be a UUID.");

  let cfg;
  try {
    cfg = config(["interceptor:read"]);
  } catch (error) {
    if (error instanceof NotConfigured) return problem(503, "NOT_CONFIGURED", error.message);
    throw error;
  }
  const result = await call(cfg, "GET", `/v1/interceptor/audit/reports/${reportId}/review-status`);
  if (result.status === 0) return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  if (result.status === 404) return problem(404, "NOT_FOUND", "No such report.");
  if (result.status !== 200) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");
  return Response.json(result.body, { headers: { "Cache-Control": "no-store" } });
}
