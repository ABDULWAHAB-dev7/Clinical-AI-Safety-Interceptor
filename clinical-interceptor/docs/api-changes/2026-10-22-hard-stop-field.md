# API amendment: `hard_stop_rules` on the audit report (2026-10-22)

Status: implemented. Additive only.

`AuditReport` (API.md 7.1.4) gains one field:

| Field | Type | Meaning |
|---|---|---|
| `hard_stop_rules` | `list[str]` | Hard-stop rules that blocked the document, for example `["R4_no_assertions"]`. Empty when none fired. |

- Existing clients that ignore unknown fields are unaffected. The model in `api/schemas.py` declares the field with a
  default of `[]`.
- `/v1/verify` (the document check) also lists the rules in `rules_fired`.
- Reason: see `docs/policy-changes/2026-10-22-hard-stop-r4.md`.
