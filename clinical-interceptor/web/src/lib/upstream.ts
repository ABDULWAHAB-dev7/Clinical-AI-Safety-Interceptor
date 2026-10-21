import { readFileSync } from "node:fs";
import { Agent, fetch as upstreamFetch } from "undici";
import { mintBearer } from "./bearer";

// Server-only. Every call to the interceptor goes through here. Credentials come from environment variables and are
// never sent to the browser. None of them may use a NEXT_PUBLIC_ prefix.
//
// Default (INTERCEPTOR_AUTH_MODE unset or "mtls"): both factors, as API.md 2.1 requires.
//   INTERCEPTOR_API_URL           base URL, for example https://interceptor.internal:8443
//   INTERCEPTOR_CLIENT_CERT_FILE  PEM client certificate registered with the interceptor
//   INTERCEPTOR_CLIENT_KEY_FILE   its private key
//   INTERCEPTOR_CA_FILE           CA that signed the interceptor's server certificate
//   INTERCEPTOR_BEARER_TOKEN      a short-lived JWT (15 minutes at most)
//
// Bearer-only demo (INTERCEPTOR_AUTH_MODE=bearer, see docs/api-changes/2026-10-21-bearer-demo.md): no client
// certificate. The token is minted per call from a server-only key, and it lasts five minutes.
//   INTERCEPTOR_API_URL
//   INTERCEPTOR_BEARER_SIGNING_KEY  the ES256 private key in PEM form
//
// Both modes also need INTERCEPTOR_PSEUDO_KEY, so the route can derive patient_pseudo_id.

export const TIMEOUT_MS = 10_000;

export class NotConfigured extends Error {}

export interface Config {
  base: string;
  token: string;
  pseudoKey: string | undefined;
}

let agent: Agent | undefined;

export function authMode(): "mtls" | "bearer" {
  return process.env.INTERCEPTOR_AUTH_MODE === "bearer" ? "bearer" : "mtls";
}

export function config(): Config {
  const base = process.env.INTERCEPTOR_API_URL;
  if (!base) throw new NotConfigured("The dashboard is not connected to an interceptor.");

  if (authMode() === "bearer") {
    const signingKey = process.env.INTERCEPTOR_BEARER_SIGNING_KEY;
    if (!signingKey) throw new NotConfigured("The dashboard is not connected to an interceptor.");
    return { base, token: mintBearer(signingKey), pseudoKey: process.env.INTERCEPTOR_PSEUDO_KEY };
  }

  const token = process.env.INTERCEPTOR_BEARER_TOKEN;
  if (!token || !process.env.INTERCEPTOR_CLIENT_CERT_FILE || !process.env.INTERCEPTOR_CLIENT_KEY_FILE) {
    throw new NotConfigured("The dashboard is not connected to an interceptor.");
  }
  return { base, token, pseudoKey: process.env.INTERCEPTOR_PSEUDO_KEY };
}

/** The mutual-TLS dispatcher. Bearer-only mode presents no certificate, so it uses the default dispatcher. */
function dispatcher(): Agent | undefined {
  if (authMode() === "bearer") return undefined;
  agent ??= new Agent({
    connect: {
      cert: readFileSync(process.env.INTERCEPTOR_CLIENT_CERT_FILE as string),
      key: readFileSync(process.env.INTERCEPTOR_CLIENT_KEY_FILE as string),
      ca: process.env.INTERCEPTOR_CA_FILE ? readFileSync(process.env.INTERCEPTOR_CA_FILE) : undefined,
    },
  });
  return agent;
}

export interface UpstreamResult {
  status: number;
  body: unknown;
}

/** Calls the interceptor. A network failure is reported as status 0 so the caller can fail closed. */
export async function call(
  cfg: Config,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<UpstreamResult> {
  let response;
  try {
    const dispatch = dispatcher();
    response = await upstreamFetch(`${cfg.base}${path}`, {
      method,
      ...(dispatch ? { dispatcher: dispatch } : {}),
      headers: {
        Authorization: `Bearer ${cfg.token}`,
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...extraHeaders,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return { status: 0, body: null };
  }
  const text = await response.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  return { status: response.status, body: parsed };
}

export function problem(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}
