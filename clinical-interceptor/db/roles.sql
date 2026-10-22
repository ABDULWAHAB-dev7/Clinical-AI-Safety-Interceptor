-- Database roles for the audit schema (DATABASE.md 6). Apply AFTER db/schema.sql (or `alembic upgrade head`), as the
-- schema owner. It is safe to run again.
--
-- The roles have no login. Your operator creates real users and grants them one of these roles, so no password or key
-- is ever stored in this repository:
--     CREATE ROLE svc_interceptor LOGIN PASSWORD '...' IN ROLE interceptor_app;
--
--   interceptor_app     the service: insert and read everything, update only the note status columns. Never deletes.
--   interceptor_audit   read only, for auditors. Sees one patient at a time (row-level security below).
--   interceptor_safety  the safety team: read, and append reviews. Sees one patient at a time.
--
-- The append-only triggers in schema.sql reject UPDATE and DELETE on the three audit tables whatever the grants are.
-- The grants here make the same rule visible to the database permission system as well.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'interceptor_app') THEN
        CREATE ROLE interceptor_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'interceptor_audit') THEN
        CREATE ROLE interceptor_audit NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'interceptor_safety') THEN
        CREATE ROLE interceptor_safety NOLOGIN;
    END IF;
END
$$;

REVOKE ALL ON intercepted_notes, audit_traces, hallucination_events, hallucination_reviews, v_hallucination_current
    FROM interceptor_app, interceptor_audit, interceptor_safety;

-- service
GRANT SELECT, INSERT ON intercepted_notes, audit_traces, hallucination_events, hallucination_reviews
    TO interceptor_app;
GRANT UPDATE (note_sha256, char_length, claim_count, pipeline_status, completed_at) ON intercepted_notes
    TO interceptor_app;
GRANT SELECT ON v_hallucination_current TO interceptor_app;

-- auditors
GRANT SELECT ON intercepted_notes, audit_traces, hallucination_events, hallucination_reviews, v_hallucination_current
    TO interceptor_audit;

-- safety team: reads, and records a review of a finding
GRANT SELECT ON intercepted_notes, audit_traces, hallucination_events, hallucination_reviews, v_hallucination_current
    TO interceptor_safety;
GRANT INSERT ON hallucination_reviews TO interceptor_safety;

-- Row-level security on the patient. The reading roles must first name the patient they are looking at:
--     SET interceptor.patient_scope = 'pid_<64 hex>';
-- A value of '*' means every patient and is meant for a break-glass account that is itself audited. Without the
-- setting a reader sees no rows. The service role is not restricted, because it serves every patient and is limited by
-- the API's own client registry.
ALTER TABLE intercepted_notes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_traces          ENABLE ROW LEVEL SECURITY;
ALTER TABLE hallucination_events  ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['intercepted_notes', 'audit_traces', 'hallucination_events'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS app_all ON %I', t);
        EXECUTE format('CREATE POLICY app_all ON %I FOR ALL TO interceptor_app USING (true) WITH CHECK (true)', t);
        EXECUTE format('DROP POLICY IF EXISTS reader_scope ON %I', t);
        EXECUTE format(
            'CREATE POLICY reader_scope ON %I FOR SELECT TO interceptor_audit, interceptor_safety '
            'USING (current_setting(''interceptor.patient_scope'', true) IN (''*'', patient_pseudo_id))', t);
    END LOOP;
END
$$;

-- A view runs with its owner's rights by default, which would skip the policies above. Run it as the caller.
ALTER VIEW v_hallucination_current SET (security_invoker = true);

-- Not covered: hallucination_reviews has no patient column, so a reader of that table sees reviews of every patient.
-- Reviews hold only a resolution, a pseudonym and a hash, never content.
