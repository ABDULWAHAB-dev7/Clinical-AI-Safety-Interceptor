"""Bearer token verification and scope checks (API.md 2.1 and 2.2).

Implemented here: the client certificate (api/mtls.py), RS256/ES256 JWT signature, audience, expiry, lifetime at most
15 minutes, scopes, and the per-client rate limit. Both factors are required on every clinical endpoint (API.md 2.1).

The default verifier denies every token (fail-closed). A verifier is only installed when a public key is configured.
"""

from typing import Optional

import jwt
from fastapi import Depends, Request

from .errors import InterceptionError
from .mtls import identify_client

AUDIENCE = "clinical-interceptor"
MAX_LIFETIME_SECONDS = 15 * 60
ALGORITHMS = ["RS256", "ES256"]

SCOPES = {
    "intercept": "interceptor:intercept",
    "decompose": "interceptor:decompose",
    "verify": "interceptor:verify",
    "audit_write": "interceptor:audit:write",
    "read": "interceptor:read",
}


class DenyAllVerifier:
    def verify(self, token):
        raise InterceptionError("AUTH_TOKEN_INVALID", "No token verifier is configured.")


class JwtVerifier:
    def __init__(self, public_key_pem):
        self.public_key = public_key_pem

    def verify(self, token):
        try:
            claims = jwt.decode(
                token,
                self.public_key,
                algorithms=ALGORITHMS,
                audience=AUDIENCE,
                options={"require": ["exp", "iat", "aud"]},
            )
        except jwt.PyJWTError:
            raise InterceptionError("AUTH_TOKEN_INVALID", "Bearer token is missing, expired, or not valid.")
        if claims["exp"] - claims["iat"] > MAX_LIFETIME_SECONDS:
            raise InterceptionError("AUTH_TOKEN_INVALID", "Bearer token lifetime exceeds 15 minutes.")
        return claims


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def can_see_patient(request: Request, pseudo_id):
    return request.app.state.mtls.allows(request.state.client_id, pseudo_id)


def authorize_patient(request: Request, pseudo_id):
    """A client may only use the patients in its registry scope (403). Reads use can_see_patient and answer 404."""
    if not can_see_patient(request, pseudo_id):
        raise InterceptionError("AUTH_SCOPE_INSUFFICIENT", "The client is not authorized for this patient.",
                                "patient_pseudo_id")


def require_scope(scope_key):
    scope = SCOPES[scope_key]

    def dependency(request: Request):
        state = request.app.state
        # Both factors are required (API.md 2.1). The certificate is checked first, then the token, then the scope.
        request.state.client_id = identify_client(request, state.mtls)
        token = _bearer(request)
        if token is None:
            raise InterceptionError("AUTH_TOKEN_INVALID", "Bearer token is missing.")
        claims = state.verifier.verify(token)
        granted = set(str(claims.get("scope", "")).split())
        if scope not in granted:
            raise InterceptionError("AUTH_SCOPE_INSUFFICIENT", f"Token lacks the scope {scope}.")
        status = state.limiter.hit(request.state.client_id)
        request.state.ratelimit = status
        if not status.allowed:
            raise InterceptionError("RATE_LIMITED", "The request rate limit was exceeded.",
                                    retry_after=status.reset_seconds)
        return claims

    return Depends(dependency)
