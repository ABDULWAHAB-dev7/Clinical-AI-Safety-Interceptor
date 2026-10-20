# Gate G4: HTTP trailers

Status: **recommendation, needs your approval.** Changing API.md 4.5 and the latency headers is an API amendment, which
also falls under Gate G3. PLAN 7.5.3 says "do not change the header silently", so the code is not changed until you
approve.

## Question

Does the chain between a client and this service pass HTTP trailers? API 3 and 4.5 use trailers for
`X-Interception-Time-MS`, `X-Interception-First-Release-MS`, and `RateLimit-*` when their value is not known at response
start.

## Server side (tested here)

uvicorn sends trailers only over HTTP/2, and only to clients that send `TE: trailers`. The OpenAI-style clients this
proxy serves use HTTP/1.1. So the trailer is not sent on a streamed response from this server (see the README, open
decision 1). The total time still appears in the `stream.complete` event.

## Gateways

Each gateway below was checked in search results. None was tested with this service.

| Gateway | What the sources say | Confidence |
| --- | --- | --- |
| Envoy | Official docs: the HTTP/1 codec drops proxied trailers by default. The `enable_trailers` option turns it on. Trailers are only sent when Envoy uses chunked encoding, which depends on the request method, Content-Length and Upgrade. | High for the default. Per-deployment config is needed. |
| NGINX | A 2020 mailing-list report describes a proxied backend's trailer disappearing. A Google-maintained fork adds upstream trailer support. Mainline status not confirmed. Trailers are only passed when the upstream speaks HTTP/1.1 chunked, and not for HEAD, 1xx, 204 or 304 responses. | Medium. Likely dropped on a standard build. |
| AWS ALB | No AWS documentation found that says trailers are supported. A third-party write-up (Rokt, predating the 2020 gRPC launch) says ALB does not support HTTP trailers. ALB's documented Transfer-Encoding values are chunked and identity only. | Low. Assume unsupported until tested. |
| Cloudflare | A 2020 post says the edge NGINX had limited trailer support. Cloudflare worked around it by converting gRPC to gRPC-web, which carries the trailer in the body. No newer source found. | Low for current behaviour. |

Across all four, trailer delivery needs explicit configuration or a non-default build, or it is unconfirmed. Clients
that use HTTP/1.1 also need to send `TE: trailers`, or the proxy may not forward them.

## Options

- **(a) Amend API.md (recommended).** This is the option PLAN 7.5.3 lists first. Timing values go in the
  `stream.complete` event and, on non-streaming responses, as headers. `RateLimit-*` becomes header-only. Its values are
  known when the request is admitted, so they never need a trailer.
- **(b) Switch to a server with trailer support.** This fixes the server side only. The gateway problem stays, so it does
  not resolve G4.

## Proposed amendment (for approval)

API 4.5, replace the trailer rules with:

- `X-Interception-First-Release-MS` is a response header on non-streaming responses. On streaming responses it is in
  `stream.complete` as `first_release_ms`. It is not sent as a trailer.
- `X-Interception-Time-MS` is a response header on non-streaming responses. On streaming responses it is in
  `stream.complete` as `interception_time_ms`. It is not sent as a trailer.
- `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` are always response headers. They are never trailers.
  The code already sends them as headers (`api/app.py`), so only the API text changes.

Clients that read these values from a stream read them from the event. No header changes for non-streaming responses.

## What changes if approved

- API.md 4.5 and the table in API 3 (the wording about trailers).
- Tests: `tests/integration/test_trailers.py` (PLAN 7.5.3) asserts the approved behaviour. The header is absent on
  streams and present in `stream.complete`.
- README open decision 1 is closed.
- No code change is needed for streaming: the server already sends no trailer there, and the value is in the event.
