"""Shared pipeline pieces: the stage-trace builder and the constants the stages use.

The document check itself is api/document.py, which runs the single verification engine (api/engine.py). The older
name-matching document pipeline was retired when /v1/verify moved onto that engine, so every route decides the same way.

Stage names follow the audit schema (DATABASE.md 3.1): INGEST, RETRIEVE, EXTRACT, VERIFY, ATTRIBUTE, INTERCEPT.
"""

import hashlib
import json
import time
from typing import Optional

from interceptor.store.records import StageRecord

MAX_DOCUMENT_CHARS = 8000  # approximates the 2,000-token ingress cap (architecture 3.2)
PIPELINE_VERSION = "0.2.0"
COMPONENTS = {
    "INGEST": "ingress",
    "RETRIEVE": "fhir-fixture-loader",
    "EXTRACT": "decompose-rules",
    "VERIFY": "verifier",
    "ATTRIBUTE": "attribution",
    "INTERCEPT": "policy",
}


def stage_row(name, input_payload, output, status, error: Optional[str] = None, started=None):
    latency = None if started is None else int((time.monotonic() - started) * 1000)
    return StageRecord(
        stage=name,
        component_name=COMPONENTS[name],
        component_version=PIPELINE_VERSION,
        input_sha256=_digest(input_payload),
        output_payload=output,
        status=status,
        error_message=error,
        latency_ms=latency,
    )


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
