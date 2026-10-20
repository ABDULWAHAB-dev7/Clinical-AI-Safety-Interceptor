"""Builds docs/API.md from the project's API text extraction, with the approved amendments applied.

    python tools/build_api_md.py --source <path to API.txt> --out docs/API.md

The source is the text extraction of API.pdf, which is Windows-1252 encoded. Every edit is anchored on a line that must
match exactly once, so a changed source fails loudly instead of being patched in the wrong place.
"""

import argparse
from pathlib import Path

CATEGORY_HEADING = "7.1.2 Hallucination Category Mapping"
CATEGORY_OLD_FIRST = "risk_rule = drug_allergy_conflict , or a CONTRADICTION where an entity is an allergy"
CATEGORY_NEW_ROWS = [
    "risk_rule = drug_allergy_conflict , or a CONTRADICTION where an entity is an allergy concept"
    "  DRUG_ALLERGY_CONFLICT",
    "CONTRADICTION where an entity is a medication with a dose or frequency"
    "                MEDICATION_DOSE_MISMATCH",
    "CONTRADICTION where an entity is a drug in an allergy class, no dose or frequency"
    "     DRUG_ALLERGY_CONFLICT",
    "CONTRADICTION where an entity is an active diagnosis"
    "                                  DIAGNOSIS_CONTRADICTION",
]
CATEGORY_NOTE = (
    "[AMENDED 2026-10-20, see docs/api-changes/2026-10-20-category-dose-mismatch.md. A dose or frequency on a drug in an "
    "allergy class is MEDICATION_DOSE_MISMATCH. A matched allergy still gives DRUG_ALLERGY_CONFLICT.]"
)

LATENCY_HEADING = "4.5 Latency Headers for Streaming"
LATENCY_OLD_FIRST = "X-Interception-First-Release-MS is sent as a response header"
LATENCY_OLD_LAST = "read trailers still get it."
LATENCY_NEW = [
    "[AMENDED 2026-10-20, see docs/api-changes/2026-10-20-latency-trailers.md. No latency value is sent as an HTTP "
    "trailer.]",
    "On streaming responses, X-Interception-Time-MS and X-Interception-First-Release-MS are not sent. The values are "
    "interception_time_ms and first_release_ms in the stream.complete event (first_release_ms is null if no content "
    "was released).",
    "On non-streaming responses, X-Interception-Time-MS is a response header. RateLimit-Limit, RateLimit-Remaining and "
    "RateLimit-Reset are always response headers.",
]

HEADER_ANCHOR = "Header                    Present on"
HEADER_NOTE = (
    "[AMENDED 2026-10-20: none of these headers is sent as a trailer. On streams the timing values are in "
    "stream.complete; see 4.5.]"
)


def _only(lines, prefix, start=0):
    hits = [i for i in range(start, len(lines)) if lines[i].lstrip().startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"expected one line starting with {prefix!r}, found {len(hits)}")
    return hits[0]


def build(source_text):
    lines = source_text.splitlines()

    # 7.1.2: replace the old rows. The allergy row wraps onto a second line, so four rows span five lines.
    heading = _only(lines, CATEGORY_HEADING)
    first = _only(lines, CATEGORY_OLD_FIRST, heading)
    if not lines[first + 1].startswith("or a drug in an allergy class"):
        raise SystemExit("category table wrap not as expected")
    if not lines[first + 2].lstrip().startswith("CONTRADICTION where an entity is a medication"):
        raise SystemExit("category table row 2 not as expected")
    if not lines[first + 3].lstrip().startswith("CONTRADICTION where an entity is an active diagnosis"):
        raise SystemExit("category table row 3 not as expected")
    lines[first : first + 4] = CATEGORY_NEW_ROWS
    lines[heading + 1 : heading + 1] = ["", CATEGORY_NOTE]

    # 4.5: replace the old block from its first line to its last.
    heading = _only(lines, LATENCY_HEADING)
    first = _only(lines, LATENCY_OLD_FIRST, heading)
    last = _only(lines, LATENCY_OLD_LAST, first)
    indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
    lines[first : last + 1] = [indent + line for line in LATENCY_NEW]

    # Section 3: a note above the header table.
    anchor = _only(lines, HEADER_ANCHOR)
    lines[anchor:anchor] = [HEADER_NOTE, ""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", default="docs/API.md")
    args = parser.parse_args()
    source = Path(args.source).read_bytes().decode("cp1252")
    body = build(source)
    header = (
        "# Clinical AI Safety Interceptor: API\n\n"
        "This file is the project's API text, extracted from API.pdf (Windows-1252 decoded) and kept as plain text so its\n"
        "tables stay aligned. Amendments are marked [AMENDED] and point to their decision records in docs/api-changes/.\n"
        "The PDF is still the original, so the same amendments must be made there.\n\n"
        "````text\n"
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(header + body + "````\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
