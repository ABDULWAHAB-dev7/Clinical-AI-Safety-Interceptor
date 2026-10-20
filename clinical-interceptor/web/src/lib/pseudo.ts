import { createHmac } from "node:crypto";

// Matches src/interceptor/core/pseudo.py: pid_ + HMAC-SHA256(key, MRN trimmed and upper-cased), hex.
export function patientPseudoId(mrn: string, key: string): string {
  if (Buffer.byteLength(key, "utf8") < 16) throw new Error("pseudonymization key must be at least 16 bytes");
  const normalized = mrn.trim().toUpperCase();
  if (!normalized) throw new Error("identifier is empty");
  return `pid_${createHmac("sha256", key).update(normalized, "utf8").digest("hex")}`;
}
