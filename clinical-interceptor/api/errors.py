"""Clinical error contract (API.md section 11).

Every error response has the same envelope. Messages never contain patient identifiers or note text.
Fail-closed: every error has fail_mode CLOSED, and clinical_impact says whether content could have been released.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

DOCS_BASE = "https://docs.example-hospital.internal/interceptor/errors"


@dataclass(frozen=True)
class CodeSpec:
    http_status: int
    clinical_impact: str  # NONE | DEGRADED | BLOCKED
    retryable: bool


# The codes used by this implementation. API.md 11.2 is the full list; the retryable column of that table is
# garbled in the PDF extraction, so retryable values here follow the code's meaning and should be checked.
CODES = {
    "VALIDATION_FAILED": CodeSpec(400, "NONE", False),
    "AUTH_TOKEN_INVALID": CodeSpec(401, "NONE", False),
    "AUTH_SCOPE_INSUFFICIENT": CodeSpec(403, "NONE", False),
    "NOTE_NOT_FOUND": CodeSpec(404, "NONE", False),
    "REPORT_NOT_FOUND": CodeSpec(404, "NONE", False),
    "UNSUPPORTED_MEDIA_TYPE": CodeSpec(415, "NONE", False),
    "INTERCEPT_NOTE_TOO_LARGE": CodeSpec(413, "NONE", False),
    "INTERCEPT_BUNDLE_TOO_LARGE": CodeSpec(413, "NONE", False),
    "INTERCEPT_BUNDLE_INVALID": CodeSpec(422, "BLOCKED", False),
    "INTERCEPT_FHIR_REFERENCE_UNRESOLVED": CodeSpec(422, "BLOCKED", False),
    "INTERCEPT_PATIENT_MISMATCH": CodeSpec(422, "BLOCKED", False),
    "INTERCEPT_CLAIM_LIMIT_EXCEEDED": CodeSpec(422, "BLOCKED", False),
    "AUDIT_CLAIM_MISMATCH": CodeSpec(422, "BLOCKED", False),
    "AUDIT_NOTE_PATIENT_MISMATCH": CodeSpec(422, "BLOCKED", False),
    "RATE_LIMITED": CodeSpec(429, "NONE", True),
    "INTERCEPT_INTERNAL_ERROR": CodeSpec(500, "BLOCKED", False),
    "INTERCEPT_VERIFIER_UNAVAILABLE": CodeSpec(503, "BLOCKED", True),
    "AUDIT_WRITE_FAILED": CodeSpec(503, "BLOCKED", True),
}


class InterceptionError(Exception):
    def __init__(self, code, message, param=None):
        super().__init__(message)
        if code not in CODES:
            raise ValueError(f"unknown error code {code}")
        self.code = code
        self.message = message
        self.param = param


def envelope(request, code, message, param=None, trace_id=None):
    spec = CODES[code]
    body = {
        "error": {
            "type": "clinical_interception_error",
            "code": code,
            "message": message,
            "param": param,
            "clinical_impact": spec.clinical_impact,
            "fail_mode": "CLOSED",
            "retryable": spec.retryable,
            "request_id": _request_id(request),
            "trace_id": trace_id,
            "docs_url": f"{DOCS_BASE}#{code}",
        }
    }
    headers = {"Retry-After": "30"} if spec.retryable else {}
    return JSONResponse(body, status_code=spec.http_status, headers=headers)


def _request_id(request):
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def register_error_handlers(app):
    @app.exception_handler(InterceptionError)
    async def _interception(request: Request, exc: InterceptionError):
        return envelope(request, exc.code, exc.message, exc.param)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(part) for part in first.get("loc", []) if part not in ("body", "query", "path", "header")]
        param = ".".join(loc) or None
        return envelope(request, "VALIDATION_FAILED", "The request body does not match the schema.", param)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # The message is generic on purpose: exception text may contain record data.
        return envelope(request, "INTERCEPT_INTERNAL_ERROR", "Unexpected interceptor fault. No content was released.")


def request_id_from(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)
