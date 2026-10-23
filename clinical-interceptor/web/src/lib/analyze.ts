import { randomUUID } from "node:crypto";
import { patientPseudoId } from "./pseudo";
import type { PatientContext } from "./patients";
import { shortPatientId } from "./report";
import { call, type Config, problem, type UpstreamResult } from "./upstream";

// Server-only. Runs decompose, verify and audit for one pasted note against one synthetic patient, the same way the
// benchmark runner does. The note text is returned to the caller and never stored by the interceptor or this app.

export { MAX_NOTE_CHARS } from "./analyze-limits";
const CLAIM_FIELDS = ["claim_id", "claim_text", "char_start", "char_end", "entities"] as const;

interface Claim {
  claim_id: string;
  claim_text: string;
  char_start: number;
  char_end: number;
  entities: unknown[];
}

function errorCode(result: UpstreamResult): string | undefined {
  const error = (result.body as { error?: { code?: string } } | null)?.error;
  return error?.code;
}

/** Maps an upstream failure to the response the browser sees. The message never contains note text. */
function failure(step: string, result: UpstreamResult): Response {
  if (result.status === 0) return problem(502, "UPSTREAM_UNREACHABLE", "The interceptor could not be reached.");
  if (result.status === 401 || result.status === 403) {
    return problem(502, "UPSTREAM_REFUSED", "The interceptor refused this app's credentials.");
  }
  const code = errorCode(result);
  if (code === "INTERCEPT_VERIFIER_UNAVAILABLE") {
    // Fail closed: the rules could not settle a statement and the NLI model is not configured. Nothing is released.
    return problem(
      422,
      code,
      "A statement in this note needs the language model, which is not configured on this server. The note was blocked, not checked.",
    );
  }
  if (code) return problem(502, code, `The interceptor stopped at ${step}.`);
  return problem(502, "UPSTREAM_ERROR", `The interceptor stopped at ${step}.`);
}

export async function analyze(cfg: Config, patient: PatientContext, text: string): Promise<Response> {
  if (!cfg.pseudoKey) {
    return problem(503, "NOT_CONFIGURED", "This server has no pseudonym key, so it cannot identify the patient.");
  }
  const patientId = patientPseudoId(patient.mrn, cfg.pseudoKey);

  const decomposed = await call(
    cfg,
    "POST",
    "/v1/interceptor/decompose",
    { patient_pseudo_id: patientId, note: { source_system: "WEB-PASTE", note_type: "PROGRESS_NOTE", text } },
    { "Idempotency-Key": randomUUID() },
  );
  if (decomposed.status >= 300) return failure("decompose", decomposed);
  const { note_id: noteId, claims } = decomposed.body as { note_id: string; claims: Claim[] };

  if (claims.length === 0) {
    return Response.json(
      { note_text: text, report: null, message: "No checkable statements were found, so this note was not checked and nothing was released." },
      { headers: { "Cache-Control": "no-store" } },
    );
  }

  const verified = await call(cfg, "POST", "/v1/interceptor/verify", {
    patient_pseudo_id: patientId,
    note_id: noteId,
    fhir_bundle: patient.bundle,
    claims: claims.map((c) => Object.fromEntries(CLAIM_FIELDS.map((f) => [f, c[f]]))),
  });
  if (verified.status >= 300) return failure("verify", verified);
  const { verify_trace_id: traceId, results } = verified.body as { verify_trace_id: string; results: unknown[] };

  const audited = await call(
    cfg,
    "POST",
    "/v1/interceptor/audit",
    { note_id: noteId, patient_pseudo_id: patientId, verify_trace_ids: [traceId], claims, results },
    { "Idempotency-Key": randomUUID() },
  );
  if (audited.status >= 300) return failure("audit", audited);

  const report = audited.body as { patient_pseudo_id: string } & Record<string, unknown>;
  return Response.json(
    {
      note_text: text,
      report: { ...report, patient_pseudo_id: shortPatientId(report.patient_pseudo_id) },
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
