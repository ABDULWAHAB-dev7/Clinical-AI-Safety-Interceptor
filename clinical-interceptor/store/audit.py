"""Append-only, hash-chained audit store (interim JSONL; PostgreSQL is the target, architecture 3.8).

Each request's records are written in one call. Records hold references and hashes only, never document or
record text (NFR-2.4). Any write failure raises AUDIT_WRITE_FAILURE, and the pipeline then withholds delivery.
"""

import hashlib
import json
import os
import pathlib
from datetime import datetime, timezone

from core.errors import ErrorClass, PipelineError

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _entries(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def write_records(self, request_id, records):
        try:
            entries = self._entries()
            prev = entries[-1]["hash"] if entries else GENESIS
            now = datetime.now(timezone.utc).isoformat()
            lines = []
            for record in records:
                body = {"ts": now, "request_id": request_id, **record, "prev_hash": prev}
                body["hash"] = _digest(body)
                prev = body["hash"]
                lines.append(json.dumps(body, sort_keys=True, default=str))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def verify(self):
        """Returns (True, None) if the chain is intact, else (False, index_of_first_bad_entry)."""
        prev = GENESIS
        for index, entry in enumerate(self._entries()):
            unhashed = {k: v for k, v in entry.items() if k != "hash"}
            if entry.get("prev_hash") != prev or _digest(unhashed) != entry.get("hash"):
                return False, index
            prev = entry["hash"]
        return True, None


def _digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
