"""Shared paths and helpers for the test suite. Run from clinical-interceptor/: python -m unittest discover -s tests -t ."""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy" / "default.yaml"
FHIR_DIR = ROOT / "fixtures" / "fhir"
EXAMPLES = ROOT / "examples"


def load_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# ---- bearer tokens for API tests: a real EC key pair, signed with ES256 and verified by JwtVerifier ----
import time  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

_KEY = ec.generate_private_key(ec.SECP256R1())
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode("ascii")
ALL_SCOPES = "interceptor:decompose interceptor:verify"


def make_token(scope=ALL_SCOPES, *, lifetime=600, aud="clinical-interceptor", key=None, issued_at=None):
    now = int(time.time()) if issued_at is None else issued_at
    claims = {"sub": "client-test", "aud": aud, "iat": now, "exp": now + lifetime, "scope": scope}
    return jwt.encode(claims, key or PRIVATE_PEM, algorithm="ES256")


def auth_headers(scope=ALL_SCOPES):
    return {"Authorization": f"Bearer {make_token(scope)}"}
