"""Keyed pseudo-identifiers (DATABASE.md A3 and section 3.6).

pid_ + HMAC-SHA256(secret_key, normalized_MRN), hex. The key lives in a KMS and never in the database. The seed
uses an obviously-fake development key so seed IDs are reproducible; that key must never be used for real data.
"""

import hashlib
import hmac
import re

PID_PATTERN = re.compile(r"^pid_[0-9a-f]{64}$")
EID_PATTERN = re.compile(r"^eid_[0-9a-f]{64}$")


def _pseudonymize(prefix, value, key):
    if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
        raise ValueError("pseudonymization key must be at least 16 bytes")
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("identifier is empty")
    digest = hmac.new(bytes(key), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}_{digest}"


def patient_pseudo_id(mrn, key):
    return _pseudonymize("pid", mrn, key)


def encounter_pseudo_id(encounter_identifier, key):
    return _pseudonymize("eid", encounter_identifier, key)
