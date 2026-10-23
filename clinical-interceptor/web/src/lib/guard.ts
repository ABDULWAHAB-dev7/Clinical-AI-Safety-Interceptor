// Server-only. Two guards for the dashboard's own routes: a per-visitor rate limit and a strict, size-capped JSON body.
//
// Why a limit here and not only at the interceptor: every visitor shares one token, and the interceptor limits by token,
// so one visitor could use up the limit for everyone. The limit here is per visitor address and per kind of call.
//
// The counters live in memory, so each server instance counts for itself. On a serverless host the effective limit is
// therefore somewhat higher than the number set. It is a brake on abuse, not an exact quota.
//
// Thresholds come from the environment (calls per minute per visitor), not from the code:
//   DASHBOARD_RATE_ANALYZE_PER_MINUTE  default 10   (each call runs three interceptor calls and the language model)
//   DASHBOARD_RATE_REVIEW_PER_MINUTE   default 30   (records an answer)
//   DASHBOARD_RATE_READ_PER_MINUTE     default 60   (reads)
//   DASHBOARD_MAX_BODY_BYTES           default 32768

import { problem } from "./upstream";

export type Bucket = "analyze" | "review" | "read";

const DEFAULTS: Record<Bucket, number> = { analyze: 10, review: 30, read: 60 };
const ENV: Record<Bucket, string> = {
  analyze: "DASHBOARD_RATE_ANALYZE_PER_MINUTE",
  review: "DASHBOARD_RATE_REVIEW_PER_MINUTE",
  read: "DASHBOARD_RATE_READ_PER_MINUTE",
};
const WINDOW_MS = 60_000;
const MAX_TRACKED = 5_000; // old entries are dropped, so the table cannot grow without bound

const windows = new Map<string, { start: number; count: number }>();

export function limitFor(bucket: Bucket, environ: Record<string, string | undefined> = process.env): number {
  const raw = Number(environ[ENV[bucket]]);
  return Number.isInteger(raw) && raw >= 1 ? raw : DEFAULTS[bucket];
}

/** The visitor's address as the host reports it. Behind Vercel, `x-real-ip` is set by the platform. */
export function visitor(request: Request): string {
  const real = request.headers.get("x-real-ip");
  if (real) return real.trim();
  const forwarded = request.headers.get("x-forwarded-for");
  return forwarded ? forwarded.split(",")[0].trim() : "unknown";
}

export interface Verdict {
  allowed: boolean;
  retryAfterSeconds: number;
}

/** Fixed one-minute windows. `now` is a parameter so tests do not need to wait. */
export function hit(key: string, limit: number, now: number = Date.now()): Verdict {
  const current = windows.get(key);
  const entry = current && now - current.start < WINDOW_MS ? current : { start: now, count: 0 };
  if (entry.count >= limit) {
    return { allowed: false, retryAfterSeconds: Math.max(1, Math.ceil((WINDOW_MS - (now - entry.start)) / 1000)) };
  }
  entry.count += 1;
  windows.set(key, entry);
  if (windows.size > MAX_TRACKED) {
    for (const [k, v] of windows) if (now - v.start >= WINDOW_MS) windows.delete(k);
    if (windows.size > MAX_TRACKED) windows.clear(); // still too many live entries: start over rather than grow
  }
  return { allowed: true, retryAfterSeconds: 0 };
}

export function resetForTests(): void {
  windows.clear();
}

/** A 429 response when the visitor is over the limit, otherwise null. */
export function limited(request: Request, bucket: Bucket): Response | null {
  const verdict = hit(`${bucket}:${visitor(request)}`, limitFor(bucket));
  if (verdict.allowed) return null;
  const response = problem(429, "RATE_LIMITED", "Too many requests. Wait a moment and try again.");
  response.headers.set("Retry-After", String(verdict.retryAfterSeconds));
  return response;
}

export type JsonResult = { ok: true; body: unknown } | { ok: false; response: Response };

/**
 * Reads a JSON body. It must declare application/json and be small, and it is measured while it is read, so a missing
 * or false Content-Length cannot get a large body through. Anything else is refused, not repaired.
 */
export async function readJson(request: Request, maxBytes: number = Number(process.env.DASHBOARD_MAX_BODY_BYTES) || 32_768): Promise<JsonResult> {
  const type = request.headers.get("content-type") ?? "";
  if (!/^application\/json\b/i.test(type)) {
    return { ok: false, response: problem(415, "UNSUPPORTED_MEDIA_TYPE", "Send the request as application/json.") };
  }
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maxBytes) {
    return { ok: false, response: problem(413, "BODY_TOO_LARGE", "The request is too large.") };
  }
  let text = "";
  if (request.body) {
    const reader = request.body.getReader();
    const decoder = new TextDecoder();
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > maxBytes) {
        await reader.cancel();
        return { ok: false, response: problem(413, "BODY_TOO_LARGE", "The request is too large.") };
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
  }
  try {
    return { ok: true, body: JSON.parse(text) };
  } catch {
    return { ok: false, response: problem(400, "BAD_REQUEST", "The request body must be JSON.") };
  }
}
