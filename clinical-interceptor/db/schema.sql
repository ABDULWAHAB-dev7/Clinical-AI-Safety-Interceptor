-- PostgreSQL audit schema (DATABASE.md section 3). Target: PostgreSQL 15 or later (gen_random_uuid is built in).
-- All timestamps are timestamptz. The three audit tables are append-only; corrections are new rows.
-- Application roles get INSERT and SELECT only. The triggers below enforce this regardless of grants.

CREATE TYPE pipeline_stage AS ENUM ('INGEST', 'EXTRACT', 'RETRIEVE', 'VERIFY', 'ATTRIBUTE', 'INTERCEPT');

CREATE TYPE nli_class AS ENUM ('ENTAILMENT', 'NEUTRAL', 'CONTRADICTION');

CREATE TYPE risk_severity AS ENUM ('CRITICAL', 'WARNING', 'SAFE');

CREATE TYPE hallucination_category AS ENUM (
    'DRUG_ALLERGY_CONFLICT',
    'MEDICATION_DOSE_MISMATCH',
    'DIAGNOSIS_CONTRADICTION',
    'LAB_VALUE_MISMATCH',
    'TEMPORAL_MISMATCH',
    'UNSUPPORTED_CLAIM',
    'OTHER'
);

-- One row per clinical note intercepted. Holds metadata and a hash only; the note text is never stored here.
CREATE TABLE intercepted_notes (
    note_id            uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_pseudo_id  text          NOT NULL
        CHECK (patient_pseudo_id ~ '^pid_[0-9a-f]{64}$'),
    encounter_pseudo_id text
        CHECK (encounter_pseudo_id IS NULL OR encounter_pseudo_id ~ '^eid_[0-9a-f]{64}$'),
    source_system      text          NOT NULL CHECK (length(source_system) BETWEEN 1 AND 64),
    note_type          text          NOT NULL,  -- e.g. 'DISCHARGE_SUMMARY', 'PROGRESS_NOTE'
    note_sha256        char(64)      NOT NULL CHECK (note_sha256 ~ '^[0-9a-f]{64}$'),
    note_storage_uri   text,                    -- pointer into the encrypted object store
    char_length        integer       NOT NULL CHECK (char_length >= 0),
    claim_count        integer       NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
    pipeline_status    text          NOT NULL DEFAULT 'RECEIVED'
        CHECK (pipeline_status IN ('RECEIVED', 'VERIFIED', 'BLOCKED', 'RELEASED', 'FAILED', 'OVERRIDDEN_BY_CLINICIAN')),  -- override 2026-10-20
    received_at        timestamptz   NOT NULL DEFAULT now(),
    completed_at       timestamptz,
    CONSTRAINT uq_note_per_patient UNIQUE (patient_pseudo_id, note_sha256),
    CONSTRAINT ck_completed_after_received CHECK (completed_at IS NULL OR completed_at >= received_at)
);

-- One row per pipeline stage execution per note: component, version, input hash, output.
CREATE TABLE audit_traces (
    trace_id           uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id            uuid           NOT NULL REFERENCES intercepted_notes (note_id) ON DELETE RESTRICT,
    patient_pseudo_id  text           NOT NULL  -- denormalized for patient-scoped audit queries
        CHECK (patient_pseudo_id ~ '^pid_[0-9a-f]{64}$'),
    stage              pipeline_stage NOT NULL,
    component_name     text           NOT NULL,
    component_version  text           NOT NULL,
    input_sha256       char(64)       NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    output_payload     jsonb          NOT NULL DEFAULT '{}'::jsonb,
    status             text           NOT NULL
        CHECK (status IN ('OK', 'ERROR', 'TIMEOUT', 'SKIPPED')),
    error_message      text,
    latency_ms         integer        CHECK (latency_ms IS NULL OR latency_ms >= 0),
    created_at         timestamptz    NOT NULL DEFAULT now(),
    CONSTRAINT ck_error_has_message CHECK (status = 'OK' OR error_message IS NOT NULL)
);

CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only (% rejected)', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER audit_traces_append_only
    BEFORE UPDATE OR DELETE ON audit_traces
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- One row per claim whose final risk_severity is CRITICAL or WARNING. SAFE claims are not recorded here.
CREATE TABLE hallucination_events (
    event_id           uuid                    PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id           uuid                    NOT NULL REFERENCES audit_traces (trace_id) ON DELETE RESTRICT,
    note_id            uuid                    NOT NULL REFERENCES intercepted_notes (note_id) ON DELETE RESTRICT,
    patient_pseudo_id  text                    NOT NULL
        CHECK (patient_pseudo_id ~ '^pid_[0-9a-f]{64}$'),
    claim_id           text                    NOT NULL
        CHECK (claim_id ~ '^clm_[0-9a-f]{12}$'),
    category           hallucination_category  NOT NULL,
    risk_severity      risk_severity           NOT NULL CHECK (risk_severity <> 'SAFE'),
    nli_class          nli_class               NOT NULL,
    risk_rule          text,
    confidence         numeric(4,3)            NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    matched_fhir_ref   text
        CHECK (matched_fhir_ref IS NULL OR matched_fhir_ref ~ '^[A-Z][A-Za-z]+/[A-Za-z0-9\-\.]{1,64}$'),
    claim_text         text                    NOT NULL CHECK (length(claim_text) <= 512),
    doc_char_start     integer                 CHECK (doc_char_start IS NULL OR doc_char_start >= 0),
    doc_char_end       integer                 CHECK (doc_char_end IS NULL OR doc_char_end >= 0),
    detected_at        timestamptz             NOT NULL DEFAULT now(),
    CONSTRAINT ck_event_span CHECK (
        doc_char_start IS NULL OR doc_char_end IS NULL OR doc_char_end > doc_char_start
    ),
    CONSTRAINT uq_event_per_trace_claim UNIQUE (trace_id, claim_id)
);

-- Review decisions are append-only. The current state of an event is its latest review (v_hallucination_current).
CREATE TABLE hallucination_reviews (
    review_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id      uuid        NOT NULL REFERENCES hallucination_events (event_id) ON DELETE RESTRICT,
    resolution    text        NOT NULL
        CHECK (resolution IN ('OPEN', 'CONFIRMED_ERROR', 'FALSE_POSITIVE', 'CORRECTED')),
    reviewer_ref  text        NOT NULL CHECK (reviewer_ref ~ '^rev_[0-9a-f]{16}$'),  -- pseudonymous reviewer ID
    reviewed_at   timestamptz NOT NULL DEFAULT now(),
    comment       text        CHECK (comment IS NULL OR length(comment) <= 2000)
);

CREATE TRIGGER hallucination_reviews_append_only
    BEFORE UPDATE OR DELETE ON hallucination_reviews
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

CREATE TRIGGER hallucination_events_append_only
    BEFORE UPDATE OR DELETE ON hallucination_events
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- Latest state per event. Events with no review are OPEN.
CREATE VIEW v_hallucination_current AS
SELECT e.*,
       COALESCE(latest.resolution, 'OPEN') AS resolution,
       latest.reviewed_at                  AS last_reviewed_at
FROM hallucination_events e
LEFT JOIN LATERAL (
    SELECT r.resolution, r.reviewed_at
    FROM hallucination_reviews r
    WHERE r.event_id = e.event_id
    ORDER BY r.reviewed_at DESC
    LIMIT 1
) latest ON true;

-- Indexes (DATABASE.md 3.5)
CREATE INDEX ix_notes_patient_received  ON intercepted_notes (patient_pseudo_id, received_at DESC);
CREATE INDEX ix_notes_received_brin     ON intercepted_notes USING BRIN (received_at);
CREATE INDEX ix_notes_backlog           ON intercepted_notes (pipeline_status)
    WHERE pipeline_status IN ('RECEIVED', 'FAILED');

CREATE INDEX ix_traces_note_stage       ON audit_traces (note_id, stage);
CREATE INDEX ix_traces_patient_created  ON audit_traces (patient_pseudo_id, created_at DESC);
CREATE INDEX ix_traces_created_brin     ON audit_traces USING BRIN (created_at);
CREATE INDEX ix_traces_payload_gin      ON audit_traces USING GIN (output_payload jsonb_path_ops);

CREATE INDEX ix_events_patient_detected ON hallucination_events (patient_pseudo_id, detected_at DESC);
CREATE INDEX ix_events_category_detected ON hallucination_events (category, detected_at DESC);
CREATE INDEX ix_events_severity_detected ON hallucination_events (risk_severity, detected_at DESC);
CREATE INDEX ix_events_trace            ON hallucination_events (trace_id);
CREATE INDEX ix_events_critical_feed    ON hallucination_events (detected_at) WHERE risk_severity = 'CRITICAL';

CREATE INDEX ix_reviews_event_reviewed  ON hallucination_reviews (event_id, reviewed_at DESC);
