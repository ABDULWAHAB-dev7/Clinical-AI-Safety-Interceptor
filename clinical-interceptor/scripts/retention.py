"""Reports which audit records are past the retention period, and can export them. It never deletes anything.

The audit tables are append-only by design (db/schema.sql), and the retention period itself is a decision for the
operator and its compliance owner (docs/decisions.md). The default here is 6 years, the usual HIPAA documentation
period, and it is not legal advice. Change it with --days or INTERCEPTOR_RETENTION_DAYS.

    python scripts/retention.py                      dry run: counts only
    python scripts/retention.py --export out.jsonl   also writes the expired notes and their audit rows to a file

Removing expired rows is a separate, deliberate step done by the database owner after the export has been archived.
This script does not provide it.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta

DEFAULT_DAYS = 6 * 365
TABLES = ("intercepted_notes", "audit_traces", "hallucination_events", "hallucination_reviews")


def cutoff(now, days):
    """Records received before this moment are expired."""
    if days < 1:
        raise ValueError("the retention period must be at least one day")
    return now - timedelta(days=days)


def days_from(environ, flag):
    if flag is not None:
        return flag
    raw = environ.get("INTERCEPTOR_RETENTION_DAYS")
    return int(raw) if raw else DEFAULT_DAYS


def expired_counts(conn, before):
    """Row counts per table for notes received before `before`, and what hangs off them."""
    notes = conn.execute("SELECT count(*) FROM intercepted_notes WHERE received_at < %s", (before,)).fetchone()[0]
    traces = conn.execute(
        "SELECT count(*) FROM audit_traces t JOIN intercepted_notes n USING (note_id) WHERE n.received_at < %s",
        (before,)).fetchone()[0]
    events = conn.execute(
        "SELECT count(*) FROM hallucination_events e JOIN intercepted_notes n USING (note_id) WHERE n.received_at < %s",
        (before,)).fetchone()[0]
    reviews = conn.execute(
        "SELECT count(*) FROM hallucination_reviews r JOIN hallucination_events e USING (event_id) "
        "JOIN intercepted_notes n ON n.note_id = e.note_id WHERE n.received_at < %s", (before,)).fetchone()[0]
    return {"intercepted_notes": notes, "audit_traces": traces, "hallucination_events": events,
            "hallucination_reviews": reviews}


def export(conn, before, path):
    """Writes one JSON line per expired row, tagged with its table. Returns the number of lines."""
    queries = {
        "intercepted_notes": "SELECT * FROM intercepted_notes WHERE received_at < %s ORDER BY received_at",
        "audit_traces": "SELECT t.* FROM audit_traces t JOIN intercepted_notes n USING (note_id) "
                        "WHERE n.received_at < %s ORDER BY t.created_at",
        "hallucination_events": "SELECT e.* FROM hallucination_events e JOIN intercepted_notes n USING (note_id) "
                                "WHERE n.received_at < %s ORDER BY e.detected_at",
        "hallucination_reviews": "SELECT r.* FROM hallucination_reviews r JOIN hallucination_events e USING (event_id) "
                                 "JOIN intercepted_notes n ON n.note_id = e.note_id "
                                 "WHERE n.received_at < %s ORDER BY r.reviewed_at",
    }
    lines = 0
    with open(path, "w", encoding="utf-8") as out:
        for table, sql in queries.items():
            cur = conn.execute(sql, (before,))
            names = [c.name for c in cur.description]
            for row in cur:
                out.write(json.dumps({"table": table, "row": dict(zip(names, row))}, default=str) + "\n")
                lines += 1
    return lines


def main(argv=None, environ=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, help=f"retention period in days (default {DEFAULT_DAYS})")
    parser.add_argument("--export", metavar="FILE", help="write the expired rows to this JSON lines file")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    before = cutoff(now or datetime.now(UTC), days_from(environ, args.days))
    dsn = environ.get("INTERCEPTOR_DATABASE_URL")
    if not dsn:
        print("INTERCEPTOR_DATABASE_URL is required", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.read_only = True  # this script can read, never change
        counts = expired_counts(conn, before)
        print(f"records received before {before.isoformat()} are past retention:")
        for table in TABLES:
            print(f"  {table}: {counts[table]}")
        if args.export:
            print(f"exported {export(conn, before, args.export)} rows to {args.export}")
        print("nothing was deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
