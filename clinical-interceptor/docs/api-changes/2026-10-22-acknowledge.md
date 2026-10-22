# API amendment: clinician acknowledgment (2026-10-22)

Status: implemented. Additive only. Not a regulatory claim: this records what a clinician chose, it does not validate it.

Source: PRD journey B ("warnings need a clinician to accept, edit or reject before the note is signed").

## `POST /v1/interceptor/acknowledge`

Scope: `interceptor:acknowledge` (new). Patient-scoped like the override.

Body: `report_id`, `span_id`, `action` (`ACCEPT` | `EDIT` | `REJECT`), `comment` (3-200), `clinician_signature` (3-200).

| action | stored resolution |
|---|---|
| ACCEPT | `FALSE_POSITIVE` |
| EDIT | `CORRECTED` |
| REJECT | `CONFIRMED_ERROR` |

- Only a `WARNING` finding can be acknowledged. A `CRITICAL` finding needs `/override` (409 `OVERRIDE_NOT_ALLOWED`).
- One review per finding. A second answer returns 409.
- The review is appended, never edited. The comment is stored as a hash, the signature as a pseudonym (`rev_...`),
  as for the override. The signature is self-asserted, not a verified identity.
- 201 returns `acknowledgment_id`, `report_id`, `span_id`, `event_id`, `resolution`, `reviewed_at`.

## `GET /v1/interceptor/audit/reports/{report_id}/review-status`

Scope: `interceptor:read`. Returns each WARNING and CRITICAL finding with its resolution (`OPEN` until answered),
`open_count`, the note's `pipeline_status` and `ready_to_sign`.

`ready_to_sign` is true only when no finding is open and the note is not `BLOCKED`. A blocked note becomes
`OVERRIDDEN_BY_CLINICIAN` through `/override`, never through acknowledgments.

## Store

`record_review(report_id, claim_id, resolution, reviewer_ref, comment, note_status=None)` and
`get_report_reviews(report_id)` on both stores. `record_override` now calls `record_review`.
