"""Server-sent event formatting (API.md 4.2).

Three kinds of event: `message` (an OpenAI-style chat.completion.chunk), `interception` (sentence.verified and
stream.complete), and `error` (the clinical error object). The stream always ends with `data: [DONE]`.
"""

import json

DONE = b"data: [DONE]\n\n"


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def chunk(completion_id, model, created, delta, finish_reason=None):
    return sse("message", {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    })
