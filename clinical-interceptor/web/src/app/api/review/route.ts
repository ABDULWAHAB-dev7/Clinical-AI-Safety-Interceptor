import { call, config, NotConfigured, problem } from "@/lib/upstream";

// Records a clinician's answer to one finding: accept, edit or reject a warning, or override a critical finding. The
// browser sends the report, the span, a reason and a signature. The token is minted here with only the scope this
// action needs, and it never reaches the browser.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "BAD_REQUEST", "The request body must be JSON.");
  }
  const { action, reportId, spanId, comment, signature } = (body ?? {}) as Record<string, unknown>;
  if (typeof reportId !== "string" || !UUID.test(reportId) || typeof spanId !== "string" || !UUID.test(spanId)) {
    return problem(400, "BAD_REQUEST", "reportId and spanId must be UUIDs.");
  }
  if (typeof comment !== "string" || typeof signature !== "string" || comment.trim().length < 3 || signature.trim().length < 3) {
    return problem(400, "BAD_REQUEST", "A reason and a signature are required.");
  }
  if (comment.length > 200 || signature.length > 200) return problem(400, "BAD_REQUEST", "Keep each entry under 200 characters.");

  const override = action === "OVERRIDE";
  if (!override && action !== "ACCEPT" && action !== "EDIT" && action !== "REJECT") {
    return problem(400, "BAD_REQUEST", "Unknown action.");
  }

  let cfg;
  try {
    cfg = config([override ? "interceptor:override" : "interceptor:acknowledge"]);
  } catch (error) {
    if (error instanceof NotConfigured) return problem(503, "NOT_CONFIGURED", error.message);
    throw error;
  }

  const result = override
    ? await call(cfg, "POST", "/v1/interceptor/override", {
        report_id: reportId,
        span_id: spanId,
        override_reason: comment.trim(),
        attending_signature: signature.trim(),
      })
    : await call(cfg, "POST", "/v1/interceptor/acknowledge", {
        report_id: reportId,
        span_id: spanId,
        action,
        comment: comment.trim(),
        clinician_signature: signature.trim(),
      });

  if (result.status === 0) return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  if (result.status === 401 || result.status === 403) {
    return problem(502, "UPSTREAM_REFUSED", "The interceptor refused this dashboard's credentials.");
  }
  if (result.status === 409) {
    return problem(409, "NOT_ALLOWED", "That finding was already answered, or it cannot be answered this way.");
  }
  if (result.status >= 300) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");
  const out = result.body as Record<string, unknown>;
  return Response.json(
    { ok: true, resolution: out.resolution ?? "FALSE_POSITIVE", pipeline_status: out.pipeline_status ?? null },
    { status: 201, headers: { "Cache-Control": "no-store" } },
  );
}
