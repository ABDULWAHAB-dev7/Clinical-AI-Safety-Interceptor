// Shapes the UI reads. String unions match the DATABASE.md enums and the API.md response models exactly.

export type Severity = "CRITICAL" | "WARNING" | "SAFE";
export type EventSeverity = "CRITICAL" | "WARNING";

export type Category =
  | "DRUG_ALLERGY_CONFLICT"
  | "MEDICATION_DOSE_MISMATCH"
  | "DIAGNOSIS_CONTRADICTION"
  | "LAB_VALUE_MISMATCH"
  | "TEMPORAL_MISMATCH"
  | "UNSUPPORTED_CLAIM"
  | "OTHER";

export type NliClass = "ENTAILMENT" | "NEUTRAL" | "CONTRADICTION";

export type SpanClassification =
  | "SOURCE_SUPPORTED"
  | "SOURCE_CONTRADICTED"
  | "NO_SOURCE_EVIDENCE"
  | "NON_CLINICAL";

export type PipelineStatus = "VERIFIED" | "BLOCKED";

/** One attribution span as the audit report returns it. Offsets are Unicode code points (API.md 1.4). */
export interface AttributionSpan {
  span_id: string;
  note_id: string;
  doc_char_start: number;
  doc_char_end: number;
  classification: SpanClassification;
  claim_id: string | null;
  result_id: string | null;
  explanation: string;
}

/** A span with its severity resolved. The report gives severity per event, so it is joined on claim_id. */
export interface ViewSpan extends AttributionSpan {
  severity: Severity;
  category: Category | null;
  matched_fhir_ref: string | null;
}

export interface HallucinationEvent {
  event_id: string;
  claim_id: string;
  category: Category;
  risk_severity: EventSeverity;
  nli_class: NliClass;
  risk_rule: string | null;
  confidence: number;
  matched_fhir_ref: string | null;
  detected_at: string;
}

export interface AuditReport {
  report_id: string;
  note_id: string;
  patient_pseudo_id: string;
  overall_risk_severity: Severity;
  pipeline_status: PipelineStatus;
  counts: Record<string, number>;
  hallucination_events: HallucinationEvent[];
  attribution_spans: AttributionSpan[];
  trace_id: string;
  created_at: string;
  idempotent_replay: boolean;
}

/** What the proxy endpoint reports (API.md 4.1.3) for a chat interception. */
export interface InterceptionResponse {
  interception: {
    status: "RELEASED" | "REDACTED" | "BLOCKED";
    note_id: string;
    trace_id: string;
    overall_risk_severity: Severity;
    redacted_spans: {
      doc_char_start: number;
      doc_char_end: number;
      risk_severity: Severity;
      claim_id: string | null;
      reason: string;
    }[];
    events: {
      event_id: string | null;
      claim_id: string;
      category: Category;
      risk_severity: EventSeverity;
      risk_rule: string | null;
    }[];
  };
}

/** The note index the API returns. It has no text: the service never stores it. */
export interface NoteIndex {
  note_id: string;
  patient_pseudo_id: string;
  note_sha256: string;
  char_length: number;
  claim_count: number;
  pipeline_status: string;
}

/** The fixture file: the note text (synthetic only) and the report it produced. */
export interface CaseData {
  case_id: string;
  meta: { note_synthetic: boolean; nli: string; verifier_version: string; generated_by?: string };
  note_text: string;
  report: AuditReport;
}
