# Vercel demo deployment

The interceptor and the dashboard run on Vercel as two projects, because they are two runtimes: a Python
serverless function and a Next.js app. Both are connected to the same GitHub repository, so a push to `main`
rebuilds both.

| Project | Root directory | Runtime | URL |
|---|---|---|---|
| `clinical-interceptor-api` | `clinical-interceptor/` | Python 3.12 serverless function (`api/index.py`) | `https://clinical-interceptor-api.vercel.app` |
| `clinical-interceptor-web` | `clinical-interceptor/web/` | Next.js 16 on Node 24 | `https://clinical-interceptor-web.vercel.app` |

The dashboard is the public face. The API is called only from the dashboard's server-side routes, never from a
browser.

## What the deployment is

It is the `vercel-demo` profile described in
[2026-10-21-bearer-demo.md](api-changes/2026-10-21-bearer-demo.md): bearer-only authentication, no client
certificate, synthetic data only. It is **not** the production topology in API.md 2.1, which needs two factors.
Do not put real records or real PHI in it.

## Environment variables

### `clinical-interceptor-api`

| Variable | Value | Why |
|---|---|---|
| `INTERCEPTOR_DATABASE_URL` | Neon pooled DSN | The audit store. `db/schema.sql` must be applied first. |
| `INTERCEPTOR_PSEUDO_KEY` | 32 random bytes, hex | Key for the patient pseudo-IDs. At least 16 bytes. |
| `INTERCEPTOR_JWT_PUBLIC_KEY` | ES256 public key, PEM | The verifier refuses every token without it. Newlines are stored as `\n`. |
| `INTERCEPTOR_AUTH_MODE` | `bearer` | Demo mode. The app refuses to start in this mode unless the profile below is set. |
| `INTERCEPTOR_DEPLOYMENT_PROFILE` | `vercel-demo` | Required guard for bearer-only mode. |
| `INTERCEPTOR_RATE_LIMIT_PER_MINUTE` | `120` | Per-token-subject limit. The dashboard makes three calls per analysed note. |

### `clinical-interceptor-web`

| Variable | Value | Why |
|---|---|---|
| `INTERCEPTOR_API_URL` | `https://clinical-interceptor-api.vercel.app` | Base URL of the interceptor. |
| `INTERCEPTOR_BEARER_SIGNING_KEY` | ES256 private key, PEM | The dashboard mints a five-minute token per call. Never sent to a browser. |
| `INTERCEPTOR_PSEUDO_KEY` | The same value as the API | The dashboard derives `patient_pseudo_id` itself. |
| `INTERCEPTOR_AUTH_MODE` | `bearer` | Selects the token minting path over the mTLS path. |

The signing key and the public key are one pair. The private key never leaves the server: it is an environment
variable, not a file in the repository. `.vercelignore` excludes `*.pem` and `.env*` from every upload.

## The database

A Neon Postgres (free tier) holds the audit schema. The schema is **not** applied by the deployment:
`api/index.py` never runs migrations, on purpose, because a function that migrates its own database on every cold
start is not a safe pattern.

Apply it once, against an empty database:

```bash
psql "$DATABASE_URL_UNPOOLED" -f db/schema.sql
```

The schema is not idempotent, so it only runs against an empty database. To re-apply it, drop the schema first.

Neon's pooled DSN is the right one for the function: the store opens a pool per process and holds a connection for
the length of one request. The unpooled DSN is for one-off work such as applying the schema.

## What the demo can and cannot do

| Capability | Status on Vercel |
|---|---|
| Decompose a note into claims | Works. |
| Verify against a FHIR bundle | Works for the deterministic rules: allergy concepts, coded conditions, labs, medication claims and dose limits. |
| Verify free-text statements | **Blocked.** The NLI checkpoint is about 700 MB and does not fit in a function bundle, so `INTERCEPTOR_NLI_MODEL_PATH` is unset and a claim that needs the model raises `VERIFICATION_ERROR`. The note is blocked, nothing is released. This is the fail-closed design working, not a bug. |
| Audit trail | Works. Every note, trace and event is written to Postgres. |
| Patient scoping | Not enforced in bearer mode. Every patient is visible to a token that holds the scope. |
| Client certificate (API.md 2.1 factor 1) | Not implemented. Vercel does not document inbound client-certificate checks. |

The dashboard's home page is a saved fixture, so it renders the full heatmap without a backend. The `/analyze`
page calls the live API. A note made only of statements the deterministic rules can settle returns a real report
with real findings. A note with a free-text statement returns `422 INTERCEPT_VERIFIER_UNAVAILABLE` and the page says
the note was blocked rather than checked.

## Deploying a change

Push to `main`. Both projects rebuild from the repository. The API project rebuilds from the `clinical-interceptor/`
directory, and the dashboard from `clinical-interceptor/web/`.

To deploy from the command line instead, run it from the repository root so the root directory setting applies:

```bash
vercel deploy --prod --project clinical-interceptor-api
vercel deploy --prod --project clinical-interceptor-web
```

## Things to check before any real use

1. The bearer-only mode has one authentication factor instead of two. It needs a clinical safety and compliance
   review before it carries any real record.
2. The NLI model is Gate G1 and is still open. Without it the interceptor blocks most free text, so the demo is not
   representative of a working system.
3. The audit schema has no row-level security and no roles; the application role can read every patient's rows.
4. The demo profile is not a clinical device and the data is synthetic.
