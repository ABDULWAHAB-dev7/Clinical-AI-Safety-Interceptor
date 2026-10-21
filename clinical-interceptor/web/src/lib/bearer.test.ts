import { createPublicKey, generateKeyPairSync, verify } from "node:crypto";
import { describe, expect, it } from "vitest";
import { AUDIENCE, DASHBOARD_SCOPES, MAX_LIFETIME_SECONDS, mintBearer } from "./bearer";

const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const privatePem = privateKey.export({ type: "pkcs8", format: "pem" }).toString();
const publicPem = publicKey.export({ type: "spki", format: "pem" }).toString();

function decode(token: string) {
  const [header, payload, signature] = token.split(".");
  const valid = verify(
    "sha256",
    Buffer.from(`${header}.${payload}`),
    { key: createPublicKey(publicPem), dsaEncoding: "ieee-p1363" },
    Buffer.from(signature, "base64url"),
  );
  return {
    header: JSON.parse(Buffer.from(header, "base64url").toString()),
    claims: JSON.parse(Buffer.from(payload, "base64url").toString()),
    valid,
  };
}

describe("mintBearer", () => {
  it("signs an ES256 token that the matching public key verifies", () => {
    const { header, valid } = decode(mintBearer(privatePem, { now: 1_000_000 }));
    expect(header).toEqual({ alg: "ES256", typ: "JWT" });
    expect(valid).toBe(true);
  });

  it("carries the audience, subject, times and dashboard scopes", () => {
    const { claims } = decode(mintBearer(privatePem, { now: 1_000_000 }));
    expect(claims.aud).toBe(AUDIENCE);
    expect(claims.sub).toBe("dashboard-demo");
    expect(claims.iat).toBe(1_000_000);
    expect(claims.exp).toBe(1_000_000 + 300);
    expect(claims.scope.split(" ")).toEqual(DASHBOARD_SCOPES);
  });

  it("never issues a token that lives longer than the interceptor allows", () => {
    const { claims } = decode(mintBearer(privatePem, { now: 1_000_000, lifetime: 999_999 }));
    expect(claims.exp - claims.iat).toBe(MAX_LIFETIME_SECONDS);
  });

  it("accepts a PEM whose newlines were escaped, as hosting dashboards often store them", () => {
    const escaped = privatePem.replace(/\n/g, "\\n");
    expect(decode(mintBearer(escaped, { now: 1_000_000 })).valid).toBe(true);
  });
});
