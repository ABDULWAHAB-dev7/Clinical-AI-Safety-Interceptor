import { describe, expect, it } from "vitest";
import fixture from "./fixtures/case-0001.json";
import { buildSegments } from "./heatmap";
import { shortPatientId, viewSpans } from "./report";
import type { CaseData, Severity, ViewSpan } from "./types";

function span(id: string, start: number, end: number, severity: Severity): ViewSpan {
  return {
    span_id: id,
    note_id: "n",
    doc_char_start: start,
    doc_char_end: end,
    classification: severity === "SAFE" ? "SOURCE_SUPPORTED" : "SOURCE_CONTRADICTED",
    claim_id: `clm_${id}`,
    result_id: null,
    explanation: "",
    severity,
    category: null,
    matched_fhir_ref: null,
  };
}

const joined = (segments: { text: string }[]) => segments.map((s) => s.text).join("");

describe("buildSegments", () => {
  it("turns a span over the first sentence into one CRITICAL segment with that text", () => {
    const text = "Patient denies known drug allergies. HbA1c 8.2%.";
    const { segments, warnings } = buildSegments(text, [span("a", 0, 36, "CRITICAL")]);
    expect(warnings).toEqual([]);
    const critical = segments.filter((s) => s.severity === "CRITICAL");
    expect(critical).toHaveLength(1);
    expect(critical[0].text).toBe("Patient denies known drug allergies.");
  });

  it("covers the whole text with no gap and no overlap", () => {
    const text = "alpha beta gamma delta";
    const { segments } = buildSegments(text, [span("a", 6, 10, "WARNING"), span("b", 17, 22, "SAFE")]);
    expect(joined(segments)).toBe(text);
    expect(segments[0].start).toBe(0);
    expect(segments[segments.length - 1].end).toBe(Array.from(text).length);
    for (let i = 1; i < segments.length; i++) expect(segments[i].start).toBe(segments[i - 1].end);
  });

  it("counts offsets in code points, so an emoji before a span shifts by one, not two", () => {
    const text = "😀 denies allergies";
    // "😀" is two UTF-16 units but one code point; "denies" starts at code point 2.
    const { segments } = buildSegments(text, [span("a", 2, 8, "CRITICAL")]);
    expect(segments.find((s) => s.severity === "CRITICAL")?.text).toBe("denies");
    expect(joined(segments)).toBe(text);
  });

  it("renders overlapping CRITICAL and SAFE spans as CRITICAL and keeps both spans", () => {
    const text = "0123456789";
    const { segments } = buildSegments(text, [span("safe", 0, 8, "SAFE"), span("crit", 4, 10, "CRITICAL")]);
    const overlap = segments.find((s) => s.start === 4 && s.end === 8);
    expect(overlap?.severity).toBe("CRITICAL");
    expect(overlap?.spans.map((s) => s.span_id)).toEqual(["crit", "safe"]);
    expect(joined(segments)).toBe(text);
  });

  it("drops spans outside the text or with end <= start, and reports them", () => {
    const text = "short";
    const { segments, warnings } = buildSegments(text, [
      span("past", 3, 99, "CRITICAL"),
      span("empty", 2, 2, "WARNING"),
      span("backwards", 4, 1, "WARNING"),
      span("negative", -1, 3, "SAFE"),
    ]);
    expect(warnings.map((w) => w.spanId).sort()).toEqual(["backwards", "empty", "negative", "past"]);
    expect(segments).toHaveLength(1);
    expect(segments[0]).toMatchObject({ text, severity: null });
  });

  it("returns one uncovered segment for empty span lists and nothing for empty text", () => {
    expect(buildSegments("abc", []).segments).toHaveLength(1);
    expect(buildSegments("", []).segments).toEqual([]);
  });
});

describe("case-0001 fixture", () => {
  const data = fixture as unknown as CaseData;
  const spans = viewSpans(data.report);
  const { segments, warnings } = buildSegments(data.note_text, spans);

  it("has no dropped spans and covers the note exactly", () => {
    expect(warnings).toEqual([]);
    expect(joined(segments)).toBe(data.note_text);
  });

  it("highlights the same three problems the pipeline reported", () => {
    const flagged = segments.filter((s) => s.severity === "CRITICAL" || s.severity === "WARNING");
    expect(flagged.map((s) => [s.severity, s.text])).toEqual([
      ["CRITICAL", "Patient denies known drug allergies"],
      ["CRITICAL", "Started amoxicillin 500 mg TID for sinusitis"],
      ["WARNING", "Blood pressure stable"],
    ]);
    expect(data.report.hallucination_events).toHaveLength(3);
  });

  it("shows only the first 8 characters of the patient id", () => {
    expect(shortPatientId("pid_" + "ab".repeat(32))).toBe("abababab");
    expect(data.report.patient_pseudo_id).toHaveLength(8);
  });
});
