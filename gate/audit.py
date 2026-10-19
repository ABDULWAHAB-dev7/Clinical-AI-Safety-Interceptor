"""Append-only JSONL audit log. Each entry carries a SHA-256 hash chained to the previous entry.

The PRD does not define an audit schema yet; this format is a placeholder.
"""

import hashlib
import json
import pathlib
from datetime import datetime, timezone

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _entries(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def append(self, event, data):
        entries = self._entries()
        body = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": data,
            "prev_hash": entries[-1]["hash"] if entries else GENESIS,
        }
        body["hash"] = _digest(body)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, sort_keys=True) + "\n")
        return body

    def verify(self):
        """Returns (True, None) if the chain is intact, else (False, index_of_first_bad_entry)."""
        prev = GENESIS
        for index, entry in enumerate(self._entries()):
            unhashed = {k: v for k, v in entry.items() if k != "hash"}
            if entry["prev_hash"] != prev or _digest(unhashed) != entry["hash"]:
                return False, index
            prev = entry["hash"]
        return True, None


def _digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
