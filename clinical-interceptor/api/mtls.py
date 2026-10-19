"""Client certificate identity (API.md 2.1; PLAN 5.3.2).

A clinical request needs a registered client certificate AND a bearer token. Two ways to learn the certificate:

1. Reverse proxy (recommended, the production path). The proxy terminates mTLS, checks the certificate against the
   hospital CA, and forwards the SHA-256 fingerprint in X-Client-Cert-SHA256 together with a shared secret in
   X-Proxy-Secret. The application accepts those headers only from a trusted proxy address and only when the secret
   matches (constant-time compare). Any other request carrying them is treated as having no certificate.
2. Direct mTLS in development. uvicorn does not pass the peer certificate to the application, so api/dev_tls.py
   adds it through the ASGI TLS extension (scope["extensions"]["tls"]["client_cert_chain"]).

The fingerprint must be in the client registry, which maps it to a client_id. The default registry is empty, so with no
configuration every clinical request is refused (fail-closed).

Not bound: the token's `sub` is not required to equal the certificate's client_id. Both must be valid, and the rate
limit is per certificate identity.
"""

import hashlib
import hmac
import json
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import Request

from .errors import InterceptionError

FINGERPRINT_HEADER = "x-client-cert-sha256"
SECRET_HEADER = "x-proxy-secret"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
PSEUDO_ID_RE = re.compile(r"^pid_[0-9a-f]{64}$")


@dataclass(frozen=True)
class MtlsConfig:
    registry: dict = field(default_factory=dict)  # fingerprint (lowercase hex SHA-256) -> client_id
    trusted_proxies: frozenset = frozenset()  # client addresses allowed to forward the fingerprint header
    proxy_secret: Optional[bytes] = None
    # client_id -> frozenset of patient pseudo-IDs the client may use. A client with no entry here may use any patient
    # (programmatic default). Registry files must say so explicitly, see from_registry_file.
    patient_scopes: dict = field(default_factory=dict)

    def allows(self, client_id, pseudo_id):
        scope = self.patient_scopes.get(client_id)
        return scope is None or pseudo_id in scope

    def is_unrestricted(self, client_id):
        return self.patient_scopes.get(client_id) is None

    @classmethod
    def from_registry_file(cls, path, trusted_proxies=(), proxy_secret=None):
        """Registry file. Every client must say which patients it may use, so access is never granted by omission:

            {"clients": [{"client_id": "...", "fingerprint_sha256": "...",
                          "patient_scope": "all" | ["pid_<64 hex>", ...]}]}
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        registry, scopes = {}, {}
        for entry in raw["clients"]:
            fingerprint = str(entry["fingerprint_sha256"]).lower()
            client_id = str(entry["client_id"]).strip()
            if not FINGERPRINT_RE.match(fingerprint) or not client_id:
                raise ValueError("invalid client registry entry")
            scope = entry.get("patient_scope")
            if scope == "all":
                pass
            elif isinstance(scope, list) and all(isinstance(p, str) and PSEUDO_ID_RE.match(p) for p in scope):
                scopes[client_id] = frozenset(scope)
            else:
                raise ValueError(f"client {client_id!r} needs patient_scope: 'all' or a list of pseudo-IDs")
            registry[fingerprint] = client_id
        return cls(registry, frozenset(trusted_proxies), proxy_secret, scopes)


def fingerprint_of_pem(pem):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def presented_fingerprint(request: Request, config: MtlsConfig):
    """The certificate fingerprint for this request, or None. Header-supplied values are only trusted from a proxy."""
    tls = request.scope.get("extensions", {}).get("tls") or {}
    chain = tls.get("client_cert_chain") or []
    if chain:
        try:
            return fingerprint_of_pem(chain[0])
        except ValueError:
            return None

    client = request.client
    if client is None or client.host not in config.trusted_proxies or not config.proxy_secret:
        return None
    supplied_secret = request.headers.get(SECRET_HEADER, "").encode("utf-8")
    if not hmac.compare_digest(supplied_secret, config.proxy_secret):
        return None
    fingerprint = request.headers.get(FINGERPRINT_HEADER, "").lower()
    return fingerprint if FINGERPRINT_RE.match(fingerprint) else None


def identify_client(request: Request, config: MtlsConfig):
    """Returns the registered client_id, or raises 403 AUTH_MTLS_REQUIRED."""
    fingerprint = presented_fingerprint(request, config)
    client_id = config.registry.get(fingerprint) if fingerprint else None
    if client_id is None:
        raise InterceptionError("AUTH_MTLS_REQUIRED", "A registered client certificate is required.")
    return client_id
