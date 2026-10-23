import { analyze } from "@/lib/analyze";
import { MAX_NOTE_CHARS } from "@/lib/analyze-limits";
import { limited, readJson } from "@/lib/guard";
import { patientById } from "@/lib/patients";
import { config, NotConfigured, problem } from "@/lib/upstream";

// Analyses one pasted note. The browser sends the note text and a synthetic patient context letter. It never sends
// or receives a pseudonym key, certificate or token.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const over = limited(request, "analyze");
  if (over) return over;
  const parsed = await readJson(request);
  if (!parsed.ok) return parsed.response;
  const body = parsed.body;
  const { text, patient } = (body ?? {}) as { text?: unknown; patient?: unknown };
  if (typeof text !== "string" || text.trim().length === 0) {
    return problem(400, "BAD_REQUEST", "Paste a note first.");
  }
  if (text.length > MAX_NOTE_CHARS) {
    return problem(413, "NOTE_TOO_LONG", `Notes are limited to ${MAX_NOTE_CHARS} characters.`);
  }
  const context = typeof patient === "string" ? patientById(patient) : undefined;
  if (!context) return problem(400, "BAD_REQUEST", "Choose a synthetic patient.");

  let cfg;
  try {
    cfg = config();
  } catch (error) {
    if (error instanceof NotConfigured) return problem(503, "NOT_CONFIGURED", error.message);
    throw error;
  }
  return analyze(cfg, context, text);
}
