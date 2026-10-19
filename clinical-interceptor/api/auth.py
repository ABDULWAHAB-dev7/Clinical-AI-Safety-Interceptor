"""Bearer token verification and scope checks (API.md 2.1 and 2.2).

Implemented here: RS256/ES256 JWT signature, audience, expiry, lifetime at most 15 minutes, and scopes.
NOT implemented in this step: the mTLS client-certificate check. API.md requires both bearer and mTLS on every clinical
endpoint. Until mTLS is wired at the internal load balancer, this service must not be exposed to clients.

The default verifier denies every token (fail-closed). A verifier is only installed when a public key is configured.
"""

from typing import Optional

import jwt
from fastapi import Depends, Request

from .errors import InterceptionError

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


def require_scope(scope_key):
    scope = SCOPES[scope_key]

    def dependency(request: Request):
        token = _bearer(request)
        if token is None:
            raise InterceptionError("AUTH_TOKEN_INVALID", "Bearer token is missing.")
        claims = request.app.state.verifier.verify(token)
        granted = set(str(claims.get("scope", "")).split())
        if scope not in granted:
            raise InterceptionError("AUTH_SCOPE_INSUFFICIENT", f"Token lacks the scope {scope}.")
        return claims

    return Depends(dependency)
