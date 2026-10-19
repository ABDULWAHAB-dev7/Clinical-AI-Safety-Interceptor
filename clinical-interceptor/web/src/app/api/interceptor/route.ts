import { readFileSync } from "node:fs";
import { Agent, fetch as upstreamFetch } from "undici";
import { shortPatientId } from "@/lib/report";

// The only code that calls the interceptor. The client certificate and the bearer token come from server-side
// environment variables and never reach the browser. None of them may carry the browser-exposed prefix.
//
//   INTERCEPTOR_API_URL           base URL, for example https://interceptor.internal:8443
//   INTERCEPTOR_CLIENT_CERT_FILE  PEM client certificate registered with the interceptor
//   INTERCEPTOR_CLIENT_KEY_FILE   its private key
//   INTERCEPTOR_CA_FILE           CA that signed the interceptor's server certificate
//   INTERCEPTOR_BEARER_TOKEN      a short-lived JWT with the interceptor:read scope

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TIMEOUT_MS = 10_000;
let agent: Agent | undefined;

function problem(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

function dispatcher(): Agent {
  agent ??= new Agent({
    connect: {
      cert: readFileSync(process.env.INTERCEPTOR_CLIENT_CERT_FILE as string),
      key: readFileSync(process.env.INTERCEPTOR_CLIENT_KEY_FILE as string),
      ca: process.env.INTERCEPTOR_CA_FILE ? readFileSync(process.env.INTERCEPTOR_CA_FILE) : undefined,
    },
  });
  return agent;
}

async function read(base: string, path: string, token: string): Promise<Response | Record<string, unknown>> {
  let response;
  try {
    response = await upstreamFetch(`${base}${path}`, {
      dispatcher: dispatcher(),
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  }
  if (response.status === 404) return problem(404, "NOT_FOUND", "No such note or report.");
  if (response.status === 401 || response.status === 403) {
    return problem(502, "UPSTREAM_REFUSED", "The interceptor refused this dashboard's credentials.");
  }
  if (!response.ok) return problem(502, "UPSTREAM_ERROR", "The interceptor returned an error.");
  return (await response.json()) as Record<string, unknown>;
}

export async function GET(request: Request) {
  const base = process.env.INTERCEPTOR_API_URL;
  const token = process.env.INTERCEPTOR_BEARER_TOKEN;
  if (
    !base ||
    !token ||
    !process.env.INTERCEPTOR_CLIENT_CERT_FILE ||
    !process.env.INTERCEPTOR_CLIENT_KEY_FILE
  ) {
    return problem(503, "NOT_CONFIGURED", "The dashboard is not connected to an interceptor.");
  }

  const { searchParams } = new URL(request.url);
  const noteId = searchParams.get("noteId") ?? "";
  const reportId = searchParams.get("reportId") ?? "";
  if (!UUID.test(noteId) || !UUID.test(reportId)) {
    return problem(400, "BAD_REQUEST", "noteId and reportId must both be UUIDs.");
  }

  const note = await read(base, `/v1/interceptor/notes/${noteId}`, token);
  if (note instanceof Response) return note;
  const report = await read(base, `/v1/interceptor/audit/reports/${reportId}`, token);
  if (report instanceof Response) return report;
  if (report.note_id !== noteId) return problem(404, "NOT_FOUND", "That report does not belong to that note.");

  // Only what the page needs. The patient id is cut to 8 characters here, so the browser never receives it in full.
  return Response.json(
    {
      note: {
        note_id: note.note_id,
        note_sha256: note.note_sha256,
        char_length: note.char_length,
        pipeline_status: note.pipeline_status,
      },
      report: { ...report, patient_pseudo_id: shortPatientId(String(report.patient_pseudo_id)) },
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
