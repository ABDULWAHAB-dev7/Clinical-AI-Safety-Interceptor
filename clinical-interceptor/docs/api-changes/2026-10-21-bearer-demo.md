# API amendment: bearer-only demo mode (2026-10-21)

Status: implemented for the `vercel-demo` profile only. Approved by the project owner for that profile on 2026-10-21
(plan approval). This is **not** approved for production or for real patient data. It needs a clinical safety and
compliance review before any use with real records.

## What changed

- `INTERCEPTOR_AUTH_MODE=bearer` (default `mtls`). In this mode the client certificate and registry are not checked.
  The bearer token is still checked: ES256 signature, audience `clinical-interceptor`, expiry, lifetime at most 15
  minutes, and the endpoint scope.
- The client identity comes from the token's `sub` claim. Rate limits apply per subject.
- Patient scope is not enforced. Every patient is visible to a token that holds the scope.
- `/v1/verify` is open to any token with the verify scope in this mode. The registry check that restricts it is skipped.
- The app refuses to start in bearer mode unless `INTERCEPTOR_DEPLOYMENT_PROFILE=vercel-demo` is set, and it needs a
  token verifier (`INTERCEPTOR_JWT_PUBLIC_KEY` or `INTERCEPTOR_JWT_PUBLIC_KEY_FILE`).
- The verifier accepts an inline PEM through `INTERCEPTOR_JWT_PUBLIC_KEY` for hosts without a filesystem.

## Why

The design requires a client certificate from a trusted proxy and a bearer token on every clinical call (API.md 2.1).
Vercel does not document inbound client-certificate checks, so the backend cannot meet that factor there. The demo
accepts the token factor alone to show the flow end to end.

## Security impact

- One factor instead of two. A leaked token is enough to call the clinical endpoints until it expires (5 minutes when
  minted by the dashboard, 15 minutes at most).
- No patient scoping. Do not use the demo with real or identifiable records.
- No NLI model on the host (it does not fit a function bundle), so every free-text claim is blocked (fail-closed).
  The dashboard states this.

## Tests

- `tests/unit/test_auth_bearer_mode.py`: valid token without a certificate, missing, wrong-audience, expired and
  under-scoped tokens, a token without a subject, the default mode still refusing a missing certificate, and the
  startup guards.
- `web/src/lib/bearer.test.ts`: the web tokens are ES256, carry the audience, subject, scopes and a 5-minute lifetime,
  and verify with the matching public key. A cross-language check confirmed that the Python verifier accepts a token
  minted by the web code.
