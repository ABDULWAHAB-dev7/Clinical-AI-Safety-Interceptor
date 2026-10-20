# Dashboard (PLAN phase 6)

A Next.js 16 and Tailwind interface that shows a note with a character-level heatmap. Each highlight is colored by
severity, and has a text label and icon as well (color is never the only signal). Selecting a highlight shows the
evidence: the classification, the explanation, and the matched FHIR resource.

```bash
npm install
npm run dev          # http://localhost:3000, shows the saved case-0001 output
npm run build && npm run lint && npm run typecheck
npm test             # heatmap unit tests (Vitest), including the emoji code-point case
npm run e2e          # Playwright against a production build, with axe (zero serious or critical violations)
```

## Pages

- `/` renders `src/lib/fixtures/case-0001.json`. The fixture is generated from the real pipeline, not written by hand:
  `python scripts/export_fixture.py --case case-0001 --out web/src/lib/fixtures/case-0001.json`.
  Statements that need the NLI model are scored by a neutral stand-in (no model is approved yet, Gate G1), so the fixture
  says so in `meta.nli` and the page shows a notice.
- `/notes/<note id>?report=<report id>` reads a live report. The interceptor does not store note text, so the reviewer
  pastes the exact text, which is checked against the recorded SHA-256 before any highlight is drawn. The text stays in
  component state only.

## Paste a note (`/analyze`)

`/analyze` sends a pasted note (up to 2000 characters) and a synthetic patient letter (A to D) to `POST /api/analyze`.
The route runs decompose, verify and audit on the interceptor, and returns the report with the text. Nothing is stored
by this app. The note goes only into component state.

Extra server variables for analysis: `INTERCEPTOR_PSEUDO_KEY` (the interceptor's pseudonym key, so the route can derive
`patient_pseudo_id`) and a bearer token with `interceptor:decompose`, `interceptor:verify`, `interceptor:audit:write` and
`interceptor:read`. A statement the rules cannot settle needs the NLI model. Until one is configured, that note is
blocked with `INTERCEPT_VERIFIER_UNAVAILABLE` and nothing is released.

## How the browser stays away from credentials

`src/app/api/interceptor/route.ts` is the only code that calls the interceptor. It reads these server-side variables
(none use a public prefix, and there is nothing in `NEXT_PUBLIC_*`):

| Variable | Meaning |
| --- | --- |
| `INTERCEPTOR_API_URL` | base URL of the interceptor |
| `INTERCEPTOR_CLIENT_CERT_FILE`, `INTERCEPTOR_CLIENT_KEY_FILE` | registered client certificate and key (PEM) |
| `INTERCEPTOR_CA_FILE` | CA that signed the interceptor's server certificate |
| `INTERCEPTOR_BEARER_TOKEN` | short-lived JWT with the `interceptor:read` scope |

The route cuts `patient_pseudo_id` to 8 characters before it answers. `src/proxy.ts` sets a per-request
Content-Security-Policy with a nonce (no inline script without it), `frame-ancestors 'none'`, and `no-store`. Nothing
is written to `localStorage` or `sessionStorage`.

## Deviations from the plan

- Severity is not on the report's attribution spans, only on its events, so `src/lib/report.ts` joins them on `claim_id`.
  A span with no event is shown as supported.
- "Three highlighted spans" in case-0001 means the three flagged statements (two critical, one warning). The two supported
  statements are also buttons, with a green underline and a "Supported" label, so the page has five highlights in all.
- The plan's heatmap example (`[0, 37)` for "Patient denies known drug allergies.") is off by one: the sentence is
  36 characters. The test uses 36.
- The CSP is set in `src/proxy.ts` (Next.js 16's replacement for middleware) instead of `next.config.js`, because the nonce
  has to change on every request. Every page is therefore rendered per request.
- Light theme only, so the severity colors keep a verified contrast.
- The live route has been run against a real mTLS interceptor by hand, but it has no automated test.
