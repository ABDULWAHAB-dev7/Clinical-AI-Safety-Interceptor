import type { Severity, ViewSpan } from "./types";

export interface Segment {
  start: number; // code points, inclusive
  end: number; // code points, exclusive
  text: string;
  /** Highest severity among the covering spans, or null for text no span covers. */
  severity: Severity | null;
  /** Every covering span, highest severity first, so a tooltip can show all of them. */
  spans: ViewSpan[];
}

export interface HeatmapWarning {
  spanId: string;
  reason: string;
}

export interface HeatmapResult {
  segments: Segment[];
  warnings: HeatmapWarning[];
}

const RANK: Record<Severity, number> = { CRITICAL: 3, WARNING: 2, SAFE: 1 };

export function severityRank(severity: Severity): number {
  return RANK[severity];
}

/**
 * Splits `text` into segments that cover it exactly, with no gap and no overlap.
 *
 * Offsets are Unicode code points, not UTF-16 units (API.md 1.4), so the text is turned into an array of code points
 * first. A span outside the text, or with end <= start, is dropped and reported in `warnings`. It is never clipped.
 * Where spans overlap, the higher severity decides the color and all overlapping spans stay on the segment.
 */
export function buildSegments(text: string, spans: ViewSpan[]): HeatmapResult {
  const points = Array.from(text);
  const length = points.length;
  const warnings: HeatmapWarning[] = [];
  const valid: ViewSpan[] = [];

  for (const span of spans) {
    const { doc_char_start: start, doc_char_end: end } = span;
    if (!Number.isInteger(start) || !Number.isInteger(end)) {
      warnings.push({ spanId: span.span_id, reason: "offsets are not integers" });
    } else if (end <= start) {
      warnings.push({ spanId: span.span_id, reason: "end is not after start" });
    } else if (start < 0 || end > length) {
      warnings.push({ spanId: span.span_id, reason: `range ${start}-${end} is outside the text (length ${length})` });
    } else {
      valid.push(span);
    }
  }

  const cuts = new Set<number>([0, length]);
  for (const span of valid) {
    cuts.add(span.doc_char_start);
    cuts.add(span.doc_char_end);
  }
  const ordered = [...cuts].sort((a, b) => a - b);

  const segments: Segment[] = [];
  for (let i = 0; i + 1 < ordered.length; i++) {
    const start = ordered[i];
    const end = ordered[i + 1];
    const covering = valid
      .filter((span) => span.doc_char_start <= start && span.doc_char_end >= end)
      .sort((a, b) => RANK[b.severity] - RANK[a.severity]);
    const previous = segments[segments.length - 1];
    if (previous && sameSpans(previous.spans, covering)) {
      previous.end = end;
      previous.text += points.slice(start, end).join("");
      continue;
    }
    segments.push({
      start,
      end,
      text: points.slice(start, end).join(""),
      severity: covering.length > 0 ? covering[0].severity : null,
      spans: covering,
    });
  }
  return { segments, warnings };
}

function sameSpans(a: ViewSpan[], b: ViewSpan[]): boolean {
  return a.length === b.length && a.every((span, i) => span.span_id === b[i].span_id);
}
