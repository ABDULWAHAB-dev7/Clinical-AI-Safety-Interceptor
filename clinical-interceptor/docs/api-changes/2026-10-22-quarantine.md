# API amendment: quarantine list, notifications, failed status (2026-10-22)

Status: implemented. Additive only.

Source: PRD journey C (a contradicted note is quarantined and the safety team is told) and journey D (verification
failure fails closed and is visible).

## `GET /v1/interceptor/quarantine`

Scope `interceptor:read`. Query: `limit` (1-200, default 50). Lists notes whose status is `BLOCKED` and not yet
overridden, newest first: `note_id`, `patient_pseudo_id`, `encounter_pseudo_id`, `note_type`, `claim_count`,
`report_id` (the latest report, so a reviewer can open it), `blocked_at`. No note or claim text. In mTLS mode only patients in the client's registry scope are listed. A note leaves
the list when `/override` moves it to `OVERRIDDEN_BY_CLINICIAN`.

## Notifications

Set `INTERCEPTOR_NOTIFY_WEBHOOK_URL` (https only). A JSON message is posted when:

- a note is blocked: `NOTE_QUARANTINED` with `note_id`, `report_id`, `patient_pseudo_id`, `rules`, `critical_count`;
- verification fails: `VERIFICATION_UNAVAILABLE` with `note_id`, `patient_pseudo_id`.

Only those fields are ever sent. Sending is best effort with a 2 second timeout and errors are swallowed, so a down
webhook cannot change a verdict. The webhook receiver is your responsibility and sits outside the audit store.

## Failed status

When verification cannot run (for example the NLI service is down), the request still fails closed with
`INTERCEPT_VERIFIER_UNAVAILABLE` (503), and now the note is also stored as `FAILED`, so it shows in the audit store
instead of being a note stuck at `RECEIVED`. A later check of the same text sets the status again.

## Store

`mark_note_failed(note_id)` and `list_notes_by_status(status, limit)` on both stores.
