"""Command line.

    python -m gate verify DRAFT.json RECORD.json --facility F [--audit audit.jsonl]
    python -m gate audit-verify audit.jsonl

Exit codes: 0 ready, 1 flagged, 2 quarantined, 3 released unverified,
            4 invalid input, 5 audit chain broken.
"""

import argparse
import json
import sys

from .audit import AuditLog
from .model import FLAGGED, QUARANTINED, QUARANTINED_UNAVAILABLE, READY, RELEASED_UNVERIFIED
from .policy import run_gate

EXIT_CODES = {
    READY: 0,
    FLAGGED: 1,
    QUARANTINED: 2,
    QUARANTINED_UNAVAILABLE: 2,
    RELEASED_UNVERIFIED: 3,
}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser("verify", help="verify a draft against a record")
    verify.add_argument("draft")
    verify.add_argument("record")
    verify.add_argument("--facility", required=True)
    verify.add_argument("--audit")

    audit = sub.add_parser("audit-verify", help="check the audit log hash chain")
    audit.add_argument("path")

    args = parser.parse_args(argv)

    if args.cmd == "audit-verify":
        valid, bad_index = AuditLog(args.path).verify()
        print(json.dumps({"valid": valid, "first_bad_index": bad_index}))
        return 0 if valid else 5

    try:
        draft = _load(args.draft)
        record = _load(args.record)
        audit = AuditLog(args.audit) if args.audit else None
        result = run_gate(draft, record, args.facility, audit=audit)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 4

    print(json.dumps(result, indent=2))
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
