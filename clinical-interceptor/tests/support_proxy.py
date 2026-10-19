"""Fixtures for proxy tests: a mock upstream model, the case-0001 output, and an app wired to it."""

import json

import httpx
from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.core.pseudo import patient_pseudo_id
from interceptor.intercept.upstream import UpstreamClient
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, security
from tests.support_bundle import BUNDLE, MRN

KEY = b"proxy-test-key-0123456789abcdef"
PID = patient_pseudo_id(MRN, KEY)
MODEL = "clinical-summarizer-v1"
OUTPUT = ("Patient denies known drug allergies. HbA1c 8.2% on 2026-09-02. "
          "Started amoxicillin 500 mg TID for sinusitis.")
REDACTED_OUTPUT = "[REDACTED: CRITICAL] HbA1c 8.2% on 2026-09-02. [REDACTED: CRITICAL]"


def chat_body(**changes):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Draft the progress note."}],
        "clinical_context": {"patient_pseudo_id": PID, "source_system": "EHR-SYNTH-01", "note_type": "PROGRESS_NOTE",
                             "fhir_bundle": BUNDLE},
    }
    return body | changes


def sse_text(text, piece=9, finish="stop"):
    """An OpenAI-style streaming body that delivers `text` in small deltas."""
    chunks = [text[i:i + piece] for i in range(0, len(text), piece)]
    lines = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}, 'finish_reason': None}]})}\n\n"
                    for c in chunks)
    lines += f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': finish}]})}\n\ndata: [DONE]\n\n"
    return lines


class MockUpstream:
    """An httpx transport that answers like an OpenAI-compatible server and records every request."""

    def __init__(self, text=OUTPUT, finish="stop", status=200, stream_piece=9, usage=None, raises=None, body=None):
        self.text, self.finish, self.status = text, finish, status
        self.stream_piece, self.raises, self.body = stream_piece, raises, body
        self.usage = usage or {"prompt_tokens": 812, "completion_tokens": 41, "total_tokens": 853}
        self.requests = []

    def handler(self, request):
        payload = json.loads(request.content)
        self.requests.append({"payload": payload, "headers": dict(request.headers), "url": str(request.url)})
        if self.raises:
            raise self.raises
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": {"message": "SECRET upstream detail"}})
        if payload.get("stream"):
            return httpx.Response(200, content=sse_text(self.text, self.stream_piece, self.finish).encode(),
                                  headers={"content-type": "text/event-stream"})
        if self.body is not None:
            return httpx.Response(200, content=self.body)
        return httpx.Response(200, json={
            "id": "chatcmpl-synth-0001", "object": "chat.completion", "created": 1792454400, "model": "gpt-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": self.text},
                         "finish_reason": self.finish}],
            "usage": self.usage})

    def client(self):
        return UpstreamClient("http://upstream.test/v1", "upstream-secret-key", {MODEL: "gpt-test"},
                              transport=httpx.MockTransport(self.handler))


def make_app(upstream=None, store=None, nli_model=None, configured=True, **security_kwargs):
    store = store if store is not None else MemoryAuditStore()
    mock = upstream if upstream is not None else MockUpstream()
    app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=store, pseudo_key=KEY,
                     verifier=JwtVerifier(PUBLIC_PEM), nli_model=nli_model,
                     upstream=mock.client() if configured else None, **(security() | security_kwargs))
    return TestClient(app), mock, store


def read_sse(response):
    """Parses an SSE response into [(event, data)]. data is a dict, or the string [DONE]."""
    events, name = [], "message"
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            events.append((name, raw if raw == "[DONE]" else json.loads(raw)))
            name = "message"
    return events
