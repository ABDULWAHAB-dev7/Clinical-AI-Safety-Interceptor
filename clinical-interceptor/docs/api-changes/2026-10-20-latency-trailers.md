# API change: latency values are not HTTP trailers (API 4.5, Gate G4)

Approved by the project owner on 2026-10-20 ("go ahead"), following docs/gates/G4-http-trailers.md, option (a) of PLAN 7.5.3.

## Change

- `X-Interception-Time-MS` is a response header on non-streaming responses. On streaming responses it is only the
  `interception_time_ms` field of the `stream.complete` event. It is never a trailer.
- `X-Interception-First-Release-MS` is replaced on streaming responses by the `first_release_ms` field of `stream.complete`.
  It is not sent as a trailer. Its value is the time to the first released chunk, or `null` if nothing was released.
- `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` are always response headers.

## Why

Gateways (Envoy, NGINX, AWS ALB, Cloudflare) do not pass HTTP/1.1 trailers by default, and uvicorn sends trailers only over
HTTP/2. See the gate memo for sources.

## Code and tests

- `intercept/stream.py` records `first_release_ms` and includes it in `stream.complete`.
- `tests/unit/test_latency_headers.py` asserts: no time header or trailer announcement on streams; both timings in
  `stream.complete`; the time header on non-streaming responses.

## Source document

Edit API.md 4.5 and the headers table in API 3 to match this record. The API.md original is not in this repository.
