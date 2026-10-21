import { createPrivateKey, sign } from "node:crypto";

// Server-only. Mints the short-lived ES256 bearer token the interceptor checks in bearer-only demo mode
// (INTERCEPTOR_AUTH_MODE=bearer). The signing key lives in INTERCEPTOR_BEARER_SIGNING_KEY and is never sent to a browser.
// The interceptor refuses any token that lives longer than 15 minutes, so tokens here last five.

export const AUDIENCE = "clinical-interceptor";
export const MAX_LIFETIME_SECONDS = 15 * 60;
export const DEFAULT_LIFETIME_SECONDS = 5 * 60;
export const DASHBOARD_SCOPES = [
  "interceptor:read",
  "interceptor:decompose",
  "interceptor:verify",
  "interceptor:audit:write",
];

function base64url(data: Buffer | string): string {
  return Buffer.from(data).toString("base64url");
}

/** An ES256 JWT with audience, subject, issue and expiry times, and the space-separated scope claim. */
export function mintBearer(
  signingKeyPem: string,
  options: { now?: number; lifetime?: number; subject?: string; scopes?: readonly string[] } = {},
): string {
  const now = options.now ?? Math.floor(Date.now() / 1000);
  const lifetime = Math.min(options.lifetime ?? DEFAULT_LIFETIME_SECONDS, MAX_LIFETIME_SECONDS);
  const key = createPrivateKey(signingKeyPem.replace(/\\n/g, "\n"));
  const header = base64url(JSON.stringify({ alg: "ES256", typ: "JWT" }));
  const payload = base64url(
    JSON.stringify({
      sub: options.subject ?? "dashboard-demo",
      aud: AUDIENCE,
      iat: now,
      exp: now + lifetime,
      scope: (options.scopes ?? DASHBOARD_SCOPES).join(" "),
    }),
  );
  const data = `${header}.${payload}`;
  // dsaEncoding "ieee-p1363" gives the raw r||s signature that JWS ES256 requires.
  const signature = sign("sha256", Buffer.from(data), { key, dsaEncoding: "ieee-p1363" });
  return `${data}.${base64url(signature)}`;
}
