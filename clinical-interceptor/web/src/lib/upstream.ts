import { readFileSync } from "node:fs";
import { Agent, fetch as upstreamFetch } from "undici";

// Server-only. Every call to the interceptor goes through here. The client certificate, its key, the CA and the bearer
// token come from environment variables and are never sent to the browser. None of them may use a NEXT_PUBLIC_ prefix.
//
//   INTERCEPTOR_API_URL           base URL, for example https://interceptor.internal:8443
//   INTERCEPTOR_CLIENT_CERT_FILE  PEM client certificate registered with the interceptor
//   INTERCEPTOR_CLIENT_KEY_FILE   its private key
//   INTERCEPTOR_CA_FILE           CA that signed the interceptor's server certificate
//   INTERCEPTOR_BEARER_TOKEN      a short-lived JWT. For reads it needs interceptor:read; for analysis it also needs
//                                 interceptor:decompose, interceptor:verify and interceptor:audit:write
//   INTERCEPTOR_PSEUDO_KEY        the interceptor's pseudonym key, so the route can derive patient_pseudo_id

export const TIMEOUT_MS = 10_000;

export class NotConfigured extends Error {}

export interface Config {
  base: string;
  token: string;
  pseudoKey: string | undefined;
}

let agent: Agent | undefined;

export function config(): Config {
  const base = process.env.INTERCEPTOR_API_URL;
  const token = process.env.INTERCEPTOR_BEARER_TOKEN;
  if (!base || !token || !process.env.INTERCEPTOR_CLIENT_CERT_FILE || !process.env.INTERCEPTOR_CLIENT_KEY_FILE) {
    throw new NotConfigured("The dashboard is not connected to an interceptor.");
  }
  return { base, token, pseudoKey: process.env.INTERCEPTOR_PSEUDO_KEY };
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
    response = await upstreamFetch(`${cfg.base}${path}`, {
      method,
      dispatcher: dispatcher(),
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
