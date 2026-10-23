import { beforeEach, describe, expect, it } from "vitest";
import { hit, limitFor, limited, readJson, resetForTests, visitor } from "./guard";

beforeEach(() => resetForTests());

const post = (body: BodyInit | null, headers: Record<string, string> = { "content-type": "application/json" }) =>
  new Request("http://localhost/api/x", { method: "POST", body, headers });

describe("rate limit", () => {
  it("allows up to the limit, then refuses with the seconds to wait, then resets", () => {
    for (let i = 0; i < 3; i++) expect(hit("k", 3, 1_000).allowed).toBe(true);
    const refused = hit("k", 3, 1_000 + 20_000);
    expect(refused).toEqual({ allowed: false, retryAfterSeconds: 40 });
    expect(hit("k", 3, 1_000 + 60_000).allowed).toBe(true);
  });

  it("counts each visitor and each kind of call separately", () => {
    expect(hit("analyze:1.1.1.1", 1, 0).allowed).toBe(true);
    expect(hit("analyze:1.1.1.1", 1, 1).allowed).toBe(false);
    expect(hit("analyze:2.2.2.2", 1, 2).allowed).toBe(true);
    expect(hit("read:1.1.1.1", 1, 3).allowed).toBe(true);
  });

  it("takes thresholds from the environment, and ignores values that are not whole numbers of at least one", () => {
    expect(limitFor("analyze", {})).toBe(10);
    expect(limitFor("analyze", { DASHBOARD_RATE_ANALYZE_PER_MINUTE: "3" })).toBe(3);
    for (const bad of ["0", "-2", "2.5", "many", ""]) expect(limitFor("review", { DASHBOARD_RATE_REVIEW_PER_MINUTE: bad })).toBe(30);
  });

  it("answers 429 with Retry-After and a generic message", async () => {
    const request = new Request("http://localhost/api/analyze", { headers: { "x-real-ip": "9.9.9.9" } });
    for (let i = 0; i < limitFor("analyze"); i++) expect(limited(request, "analyze")).toBeNull();
    const response = limited(request, "analyze");
    expect(response?.status).toBe(429);
    expect(Number(response?.headers.get("Retry-After"))).toBeGreaterThanOrEqual(1);
    expect((await response?.json()).error.code).toBe("RATE_LIMITED");
  });

  it("identifies the visitor from the platform header, then the first forwarded address", () => {
    expect(visitor(new Request("http://x", { headers: { "x-real-ip": " 1.2.3.4 ", "x-forwarded-for": "5.6.7.8" } }))).toBe("1.2.3.4");
    expect(visitor(new Request("http://x", { headers: { "x-forwarded-for": "5.6.7.8, 10.0.0.1" } }))).toBe("5.6.7.8");
    expect(visitor(new Request("http://x"))).toBe("unknown");
  });
});

describe("readJson", () => {
  it("parses a small JSON body", async () => {
    expect(await readJson(post('{"a":1}'))).toEqual({ ok: true, body: { a: 1 } });
  });

  it("refuses another content type with 415", async () => {
    const result = await readJson(post('{"a":1}', { "content-type": "text/plain" }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(415);
  });

  it("refuses a body over the limit with 413, even when no length is declared", async () => {
    const result = await readJson(post(JSON.stringify({ text: "x".repeat(500) })), 100);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(413);

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"text":"' + "x".repeat(300)));
        controller.enqueue(new TextEncoder().encode('"}'));
        controller.close();
      },
    });
    const chunked = new Request("http://localhost/api/x", { method: "POST", body: stream, headers: { "content-type": "application/json" }, duplex: "half" } as RequestInit);
    const second = await readJson(chunked, 100);
    expect(second.ok).toBe(false);
    if (!second.ok) expect(second.response.status).toBe(413);
  });

  it("refuses malformed JSON and an empty body with 400", async () => {
    for (const body of ["nope", ""]) {
      const result = await readJson(post(body));
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.response.status).toBe(400);
    }
  });
});
