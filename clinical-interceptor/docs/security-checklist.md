# Security checklist (audited 2026-10-23)

Six checks, applied to the interceptor API and the dashboard. Each line says what was found and where it is enforced.
This is an engineering check, not a security certification or a penetration test. The demo deployment still has no user
login (see `docs/deployment.md`), so it is for synthetic data only.

## 1. Rate limiting

| Where | Limit | Notes |
|---|---|---|
| API, every clinical call | `INTERCEPTOR_RATE_LIMIT_PER_MINUTE` (default 60) per client identity | `api/ratelimit.py`. In memory, so each instance counts alone. |
| Dashboard, analyse | `DASHBOARD_RATE_ANALYZE_PER_MINUTE` (default 10) per visitor address | Each call runs three interceptor calls and the language model. |
| Dashboard, review | `DASHBOARD_RATE_REVIEW_PER_MINUTE` (default 30) per visitor address | |
| Dashboard, reads | `DASHBOARD_RATE_READ_PER_MINUTE` (default 60) per visitor address | |

Why the dashboard limit exists: all visitors share one API token, and the API limits by token, so one visitor could use
up the allowance for everyone. A refused call is a 429 with `Retry-After` and a generic message. Thresholds are
environment variables, not constants. The dashboard has no login, signup or password-reset routes, so there are no
authentication routes to give stricter limits or backoff. If a login is added, it needs per-address and per-account
limits with backoff (not a hard lockout), as in the checklist.

Known limit: counters are per instance, so on a serverless host the real limit is somewhat higher than the number set.
A shared store (for example Redis) is the fix and is not built.

## 2. Input validation

- API: every body is a Pydantic model with `extra="forbid"`, typed ids with patterns, bounded lengths, a 2 MiB body cap
  and an `Idempotency-Key` check. A bad body is a 400 with the field named, never repaired.
- Dashboard routes: `lib/guard.ts` `readJson` requires `application/json` (415 otherwise), caps the body at
  `DASHBOARD_MAX_BODY_BYTES` (default 32 KB, measured while reading, so a false or missing `Content-Length` does not get
  past it, 413) and rejects bad JSON (400). Each route then checks its own fields: UUIDs for ids, a fixed list of actions
  and patients, 3 to 200 characters for a reason and a name, and 2000 characters for a note. Nothing is sanitised and
  accepted: a value that does not match is refused.

## 3. Secrets

- No private key, token or password is tracked in git, and none is in the history (searched for common token shapes,
  private-key headers, and `.pem`, `.key` and `.env` files). `seed/certs/` and `.env*` are ignored, and `.vercelignore`
  excludes `*.pem` and `.env*` from uploads.
- The only tracked env file is `.env.example`, which holds placeholders.
- The dashboard reads every secret from server-side environment variables. Nothing uses a `NEXT_PUBLIC_` prefix. The
  browser never receives a token, a signing key or a full patient id.
- Rule of this project: a token pasted into a chat is used for one command and never written to a file, git config or
  remote URL. Revoke any token that has been pasted anywhere.

## 4. File uploads

There are none. The API has no multipart endpoint and the dashboard has no file input, so there is nothing to validate or
store. If one is ever added, it needs type, size and content checks, storage outside the web root, and no execution.

## 5. Error handling and information leakage

- API: `api/errors.py` has handlers for the app's own errors, for validation errors and a catch-all for anything else.
  Responses use one envelope with a code and a short message. A database failure is reported as `AUDIT_WRITE_FAILED`
  with no driver text.
- Dashboard routes return fixed messages (`UPSTREAM_ERROR`, `NOT_FOUND`, and so on) and never pass an upstream error body
  on. Pages that fail show a fixed message (`app/error.tsx`), and a missing page shows `app/not-found.tsx`.
- Production Next.js does not send stack traces to the browser. Details stay in the server log.

## 6. Dependency vulnerabilities

- `npm audit`: 0 vulnerabilities.
- `pip-audit` on `requirements.lock.txt`: no known vulnerabilities.

Rerun both before every release:

```bash
cd web && npm audit
pip-audit -r requirements.lock.txt
```
