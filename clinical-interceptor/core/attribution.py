"""Stage 5: maps verdicts to character spans of the original document (architecture 3.6)."""

from .models import SpanRecord, Verdict

PRECEDENCE = {
    Verdict.CONTRADICTED: 3,
    Verdict.VERIFICATION_ERROR: 2,
    Verdict.UNGROUNDED: 1,
    Verdict.SUPPORTED: 0,
    Verdict.EXCLUDED: 0,
}


def build_spans(document, assertions, records):
    """One span per assertion, ordered by start. Overlapping spans merge, keeping the highest-precedence verdict."""
    by_id = {r.assertion_id: r for r in records}
    spans = []
    for assertion in sorted(assertions, key=lambda a: a.span_start):
        record = by_id[assertion.assertion_id]
        current = SpanRecord(
            start=assertion.span_start,
            end=assertion.span_end,
            verdict=record.verdict,
            assertion_ids=[assertion.assertion_id],
            evidence=list(record.evidence),
            text=document[assertion.span_start:assertion.span_end],
        )
        if spans and current.start < spans[-1].end:
            prev = spans.pop()
            end = max(prev.end, current.end)
            spans.append(
                SpanRecord(
                    start=prev.start,
                    end=end,
                    verdict=max(prev.verdict, current.verdict, key=PRECEDENCE.__getitem__),
                    assertion_ids=prev.assertion_ids + current.assertion_ids,
                    evidence=prev.evidence + current.evidence,
                    text=document[prev.start:end],
                )
            )
        else:
            spans.append(current)
    return spans
