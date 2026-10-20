# Clinical AI Safety Interceptor: API

This file is the project's API text, extracted from API.pdf (Windows-1252 decoded) and kept as plain text so its
tables stay aligned. Amendments are marked [AMENDED] and point to their decision records in docs/api-changes/.
The PDF is still the original, so the same amendments must be made there.

````text
API.md -- Clinical Interceptor API Specification

   Status: Draft v0.1 · Contract version: 1.0.0 · Base path: /v1 Source of truth: PRD.md,
   ARCHITECTURE.md, DATABASE.md. Where this document and DATABASE.md disagree on a field, enum, or
   pattern, DATABASE.md wins and this document must be corrected.

0. Assumptions & Open Items

#   Assumption                                     Reconcile against

A1  The interceptor is deployed inside the         ARCHITECTURE: deployment

    hospital network. Only the internal EHR

    gateway and the clinical AI client can reach

    it.

A2  Upstream LLM calls go through a                ARCHITECTURE: model routing

    configured provider adapter. Clients

    address upstream models by a logical

    model name, never by provider URL.

A3  The interceptor fails closed. If verification PRD: patient-safety requirements

    or audit cannot complete, no clinical text is

    released to the client. Fail-open is not

    supported on any endpoint.

A4  Patient identity is pseudonymized on           Privacy requirements

    receipt. Raw MRNs and names are removed

    before any persistence or logging

    (DATABASE.md A3).

A5  Latency targets in §9 are proposals. They PRD: performance

    must be checked against the SLOs in

    PRD.md.

A6  The OpenAI Chat Completions wire format ARCHITECTURE: client compatibility

    is used for the proxy endpoint, so existing

    clients can connect with a base-URL

    change.

Open items

             Confirm the maximum streaming hold time per sentence ( max_sentence_wait_ms , default 2000 ms).
             Confirm the rate-limit quotas per client identity.
             Confirm whether BLOCK_RESPONSE must also be available for non-streaming calls (currently supported).

1. Base URL, Versioning & Conventions

1.1 Base URL

Environment                            Base URL
Production
Staging                                https://clinical-interceptor.example-
                                       hospital.internal
Development
                                       https://clinical-interceptor.staging.example-
                                       hospital.internal

                                       https://clinical-interceptor.dev.example-
                                       hospital.internal

All URLs in this document are relative to the base URL. The placeholder hostnames must be replaced at
deployment time.

1.2 Versioning Strategy

             The major version is in the URL path: /v1/... . A breaking change creates /v2/... . Both versions run
             side by side for at least one deprecation window.
             Within v1 , only additive changes are allowed: new optional request fields, new response fields, new
             enum values in response-only fields. Clients must ignore unknown response fields.
             Enum values that drive safety decisions ( nli_class , risk_severity , classification ) are closed sets.
             Adding a value is a breaking change and requires /v2 .
             The contract version is returned in the X-Interceptor-Version header on every response, for example
             1.0.0 .
             Deprecated endpoints return Deprecation: true and Sunset: <HTTP-date> (RFC 8594) headers at least 6
             months before removal.

1.3 Content Types

Direction               Media type
Request body            application/json; charset=utf-8
Non-streaming response  application/json; charset=utf-8
Streaming response      text/event-stream; charset=utf-8
Errors                  application/json; charset=utf-8 (always, including for
                        streaming requests that fail before the first byte)

1.4 Text Offsets

All character offsets are Unicode code point indices, 0-based, with an exclusive end. This matches
DATABASE.md §4.1. Byte offsets are never used in the API.

2. Authentication & Authorization

2.1 Mechanisms

Mechanism                Applies to                             Details
Bearer token             All /v1/* endpoints except health
                                                                OIDC-issued JWT (RS256 or ES256).
mTLS                     All clinical endpoints:                Header: Authorization: Bearer
                         /v1/chat/completions/intercept and     <token> . Audience ( aud ) must be
None                     /v1/interceptor/*                      clinical-interceptor . Maximum
                                                                lifetime 15 minutes.
                         GET /v1/health , GET /v1/health/ready
                                                                The client presents an X.509
                                                                certificate issued by the hospital
                                                                internal CA. The certificate subject
                                                                must map to a registered
                                                                client_id in the client registry.

                                                                Reachable only from the internal
                                                                load balancer.

A clinical request must satisfy both the bearer token and the mTLS certificate. Either one alone is rejected.

2.2 Scopes

Scope                    Grants
interceptor:intercept    POST /v1/chat/completions/intercept
interceptor:decompose    POST /v1/interceptor/decompose
interceptor:verify       POST /v1/interceptor/verify
interceptor:audit:write  POST /v1/interceptor/audit
interceptor:read         GET /v1/interceptor/notes/{note_id} , GET
                         /v1/interceptor/audit/reports/{report_id}

2.3 Required Headers

Header                   Required                             Description
Authorization            Yes                                  Bearer <JWT>
Content-Type             Yes, for bodies                      application/json
X-Request-Id             No                                   Client-generated UUIDv4. Echoed in the response.
                                                              Generated by the server if absent.
Idempotency-Key          Yes, for POST /v1/interceptor/audit  UUIDv4. See §8.3.
                         and POST /v1/interceptor/decompose
traceparent              No                                   W3C Trace Context. Propagated to all internal
                                                              stages.
Accept                   No                                   text/event-stream to request streaming on the
                                                              proxy endpoint, or application/json . The stream
                                                              body field takes precedence.

3. Common Response Headers

[AMENDED 2026-10-20: none of these headers is sent as a trailer. On streams the timing values are in stream.complete; see 4.5.]

Header                    Present on                                  Description
X-Request-Id              All responses
                          All clinical endpoints                      Echo of the request ID, or the server-generated
X-Interception-Time-MS                                                ID.
                          Streaming proxy responses only
X-Interception-First-                                                 Total server-side interception time in
Release-MS                All clinical endpoints that write an audit  milliseconds, as an integer. For streaming
                          trace                                       responses this header is sent as an HTTP trailer
X-Trace-Id                Proxy endpoint only                         after the body (see §4.5).
                          All responses
X-Interception-Status     All authenticated responses                 Milliseconds from request receipt to the first
X-Interceptor-Version                                                 released content chunk. Sent as a response
RateLimit-Limit ,                                                     header if known at response start, otherwise as a
RateLimit-Remaining ,                                                 trailer.
RateLimit-Reset
Retry-After                                                           UUID of the primary audit_traces row
                                                                      (DATABASE.md §3.3).

                                                                      One of RELEASED , REDACTED , BLOCKED .

                                                                      Contract version, for example 1.0.0 .

                                                                      IETF RateLimit header fields.

                          429 and 503 responses                       Seconds, or an HTTP-date.

4. Transparent LLM Proxy

4.1 POST /v1/chat/completions/intercept

Receives a clinical prompt and the patient's FHIR bundle. It forwards the prompt to the upstream model, then
verifies the model's output before releasing it to the client. The response follows the OpenAI Chat
Completions format, with one additional top-level object, interception .
Scopes: interceptor:intercept · Auth: Bearer + mTLS

4.1.1 Request Body

Field             Type             Required      Constraints               Description
model             string                Yes      1-64 chars, ^[a-z0-9][a-
                                                 z0-9._-]*$                Logical model name from
messages          array of              Yes      1-100 items               the routing table.
                  ChatMessage
                                        No       Default false             OpenAI-compatible
stream            boolean               No       0.0-2.0                   messages. role is one of
temperature       number                No       1-8192                    system , user , assistant .
max_tokens        integer               Yes      --
clinical_context  ClinicalContext                                          true for SSE output.

                                                                           Passed upstream.

                                                                           Passed upstream.

                                                                           Patient identity and FHIR
                                                                           facts.

Field              Type                Required               Constraints         Description
interception       InterceptionPolicy       No                Defaults in §4.1.4
                                                                                  Per-request policy
                                                                                  overrides.

ChatMessage      Type                                                  Required   Constraints
                 "system" | "user" | "assistant"                            Yes   --
 Field           string                                                     Yes   1-200,000 chars
 role
 content

ClinicalContext

Field                Type              Required   Constraints          Description
patient_pseudo_id    string                 Yes   ^pid_[0-9a-f]{64}$
                                                                       Must match the single Patient in the
encounter_pseudo_id string                  No    ^eid_[0-9a-f]{64}$   bundle. Otherwise 422
                                            Yes   1-64 chars           INTERCEPT_PATIENT_MISMATCH .
source_system        string                 No    1-64 chars
                                            Yes   1-500 entries;  1.5  Stored in
note_type            string                       MB serialized        intercepted_notes.encounter_pseudo_id .

fhir_bundle          FHIRBundle                                        Originating EHR, for example EHR-SYNTH-
                                                                       01 .

                                                                       For example PROGRESS_NOTE ,
                                                                       DISCHARGE_SUMMARY .

                                                                       FHIR R4 Bundle with type = collection .

FHIRBundle (subset)

Field                Type                         Required             Constraints
resourceType         "Bundle"                          Yes
type                 "collection"                      Yes             --

entry                array                             Yes             Other bundle types are rejected with 422
                                                                       INTERCEPT_BUNDLE_INVALID .

                                                                       Each entry has resource with one of:
                                                                       Patient , Condition , Observation ,
                                                                       AllergyIntolerance , Encounter . Other
                                                                       types are ignored and listed in warnings .

Resources are validated against the Pydantic models in DATABASE.md §1. A resource that fails validation
causes a 422 INTERCEPT_BUNDLE_INVALID for the whole request. Partial bundles are not accepted.

InterceptionPolicy

Field                        Type                 Default              Description
warning_action                                    "ANNOTATE"
                             "ANNOTATE" |                              What to do with sentences whose highest
critical_action              "REDACT_SENTENCE"                         severity is WARNING . ANNOTATE releases
                                                                       the text and adds an event.
                             "REDACT_SENTENCE"    "REDACT_SENTENCE"
                             |                                         What to do with sentences whose highest
                             "BLOCK_RESPONSE"                          severity is CRITICAL . BLOCK_RESPONSE
                                                                       withholds the whole response.

Field                      Type     Default  Description
min_confidence_entailment  number   0.85
                                    2000     Threshold for rule 5 in the severity policy
max_sentence_wait_ms       integer  false    (DATABASE.md §2.4). Range 0.50-1.00.

include_claims             boolean           Maximum time to verify one sentence
                                             before it is withheld. Range 200-10000.

                                             When true , the interception object
                                             includes the full claims and results
                                             arrays.

4.1.2 Example Request: Synthetic Allergy Mismatch

The bundle is synthetic and matches DATABASE.md §4.2 ( case-0001 ). The pid_ value below is a valid 64-hex-
character placeholder.

               {
                  "model": "clinical-summarizer-v1",
                  "stream": false,
                  "messages": [
                      {
                          "role": "system",
                          "content": "You are a clinical documentation assistant. Draft notes using only documented facts."
                      },
                      {
                          "role": "user",
                          "content": "Draft the progress note for today's follow-up visit from the attached record."
                      }
                  ],
                  "clinical_context": {
                      "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                      "encounter_pseudo_id": null,
                      "source_system": "EHR-SYNTH-01",
                      "note_type": "PROGRESS_NOTE",
                      "fhir_bundle": {
                          "resourceType": "Bundle",
                          "type": "collection",
                          "entry": [
                             {
                                 "resource": {
                                     "resourceType": "Patient",
                                     "id": "pat-0001",
                                     "gender": "female",
                                     "birthDate": "1968-03-14",
                                     "active": true
                                 }
                             },
                             {
                                 "resource": {
                                     "resourceType": "AllergyIntolerance",
                                     "id": "allergy-penicillin-001",
                                     "clinicalStatus": { "coding": [{ "system":

"http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active" }] },
                                     "verificationStatus": { "coding": [{ "system":

"http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "confirmed" }] },
                                     "type": "allergy",
                                     "category": ["medication"],
                                     "criticality": "high",
                                     "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "91936005", "display": "Allergy

to penicillin" }] },
                                     "patient": { "reference": "Patient/pat-0001" },
                                     "reaction": [{ "manifestation": [{ "coding": [{ "system": "http://snomed.info/sct", "code":

"271807003", "display": "Eruption of skin" }] }], "severity": "moderate" }]
                                 }

                             },
                             {

                                 "resource": {
                                     "resourceType": "Condition",
                                     "id": "cond-t2dm-001",
                                     "clinicalStatus": { "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/condition-

clinical", "code": "active" }] },
                                     "verificationStatus": { "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/condition-

ver-status", "code": "confirmed" }] },
                                     "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "44054006", "display":

"Diabetes mellitus type 2" }] },
                                     "subject": { "reference": "Patient/pat-0001" },
                                     "onsetDateTime": "2019-05-02T00:00:00Z"

                                 }
                             },
                             {

                                 "resource": {
                                     "resourceType": "Observation",

                                       "id": "obs-hba1c-001",
                                       "status": "final",
                                       "code": { "coding": [{ "system": "http://loinc.org", "code": "4548-4", "display": "Hemoglobin
  A1c/Hemoglobin.total in Blood" }] },
                                       "subject": { "reference": "Patient/pat-0001" },
                                       "effectiveDateTime": "2026-09-02T08:30:00Z",
                                       "valueQuantity": { "value": 8.2, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%" }
                                    }
                                }
                            ]
                         }
                     },
                     "interception": {
                         "warning_action": "ANNOTATE",
                         "critical_action": "REDACT_SENTENCE",
                         "min_confidence_entailment": 0.85,
                         "max_sentence_wait_ms": 2000,
                         "include_claims": true
                     }
                 }

4.1.3 Example Response: Non-Streaming, Allergy Conflict Redacted

The upstream model produced an output that denies the penicillin allergy and then orders amoxicillin. Both
sentences are CRITICAL , so both are redacted. doc_char_start and doc_char_end refer to the upstream output
text, not the input note.

Upstream output, before redaction ( doc offsets refer to this text):

  Patient denies known drug allergies. HbA1c 8.2% on 2026-09-02. Started amoxicillin 500 mg TID for sinusitis.

HTTP status 200 OK :

                 {
                     "id": "chatcmpl-synth-0001",
                     "object": "chat.completion",
                     "created": 1792454400,
                     "model": "clinical-summarizer-v1",
                     "choices": [
                         {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "[REDACTED: CRITICAL] HbA1c 8.2% on 2026-09-02. [REDACTED: CRITICAL]"
                            },
                            "finish_reason": "stop"
                         }
                     ],
                     "usage": {
                         "prompt_tokens": 812,
                         "completion_tokens": 41,
                         "total_tokens": 853
                     },
                     "interception": {
                         "status": "REDACTED",
                         "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                         "trace_id": "0c7e5a21-3b9d-4f0e-8a6c-1d2e3f4a5b6c",
                         "overall_risk_severity": "CRITICAL",
                         "redacted_spans": [
                            {
                                "doc_char_start": 0,
                                "doc_char_end": 37,
                                "risk_severity": "CRITICAL",
                                "claim_id": "clm_00000000a001",
                                "reason": "Output denies a documented penicillin allergy."
                            },
                            {
                                "doc_char_start": 64,
                                "doc_char_end": 106,
                                "risk_severity": "CRITICAL",
                                "claim_id": "clm_00000000a003",
                                "reason": "Output orders amoxicillin for a patient with a documented penicillin allergy."
                            }
                         ],
                         "events": [
                            {
                                "event_id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                                "claim_id": "clm_00000000a001",
                                "category": "DRUG_ALLERGY_CONFLICT",
                                "risk_severity": "CRITICAL",
                                "risk_rule": "contradiction_high_risk"
                            },
                            {
                                "event_id": "e2f3a4b5-d6e7-4f8a-9b0c-1d2e3f4a5b6c",
                                "claim_id": "clm_00000000a003",
                                "category": "DRUG_ALLERGY_CONFLICT",
                                "risk_severity": "CRITICAL",
                                "risk_rule": "drug_allergy_conflict"
                            }
                         ],
                         "warnings": [],
                         "claims": [],
                         "results": []
                     }

                 }

With include_claims: true , the claims and results arrays are populated with the full AtomicProposition and
VerificationResult objects (see §5.1 and §6.1 for shapes).

4.1.4 Interception Status Semantics

interception.status       Meaning            choices[0].finish_reason       Content returned
RELEASED                                     stop (or upstream value)       Full upstream text.
REDACTED                  Every sentence is  stop
                          SAFE , or WARNING                                 Text with redacted
BLOCKED                   with               content_filter                 sentences replaced by
                          warning_action                                    [REDACTED: <SEVERITY>] .
                          = ANNOTATE .
                                                                            content is null . The
                          At least one                                      reason is in
                          sentence is                                       interception.reason .
                          CRITICAL with
                          critical_action
                          =
                          REDACT_SENTENCE ,
                          or WARNING with
                          REDACT_SENTENCE .

                          critical_action
                          =
                          BLOCK_RESPONSE
                          and at least one
                          CRITICAL
                          sentence, or the
                          upstream output
                          could not be
                          verified within
                          limits.

A BLOCKED response is HTTP 200, not an error. The request was processed correctly and the policy decided to
withhold the content. Clients that handle content_filter already support this case. Use an error response (§11)
only when the interceptor itself could not run.

4.2 Streaming Mode

When stream: true , the response is text/event-stream . Each sentence is verified before its text is sent. The
interceptor holds back at most one sentence at a time.

SSE event types

event: name                                  Payload                        Meaning
(default, no event name)                     chat.completion.chunk object
                                                                            Content delta. Same shape as
interception                                 Interception event object      OpenAI streaming chunks.
error                                        (below)
                                             Clinical error object (§11.1)  Verification result for a
                                                                            sentence, or the final summary.

                                                                            Stream terminated with an error.
                                                                            No further content follows.

Interception event types

type                      Fields                                            When
sentence.verified                                                           After each sentence is verified.
                          sentence_index , doc_char_start ,
                          doc_char_end , claim_ids , risk_severity ,
                          action ( RELEASED | ANNOTATED | REDACTED )

  type                        Fields                         When
  stream.complete
                              status , note_id , trace_id ,  Last event, before data:
Example stream (abbreviated)  overall_risk_severity ,        [DONE] .
                              interception_time_ms

event: message
data: {"id":"chatcmpl-synth-0002","object":"chat.completion.chunk","created":1792454400,"model":"clinical-summarizer-
v1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

event: interception
data: {"type":"sentence.verified","sentence_index":0,"doc_char_start":0,"doc_char_end":37,"claim_ids":
["clm_00000000a001"],"risk_severity":"CRITICAL","action":"REDACTED"}

event: message
data: {"id":"chatcmpl-synth-0002","object":"chat.completion.chunk","created":1792454400,"model":"clinical-summarizer-
v1","choices":[{"index":0,"delta":{"content":"[REDACTED: CRITICAL] "},"finish_reason":null}]}

event: message
data: {"id":"chatcmpl-synth-0002","object":"chat.completion.chunk","created":1792454400,"model":"clinical-summarizer-
v1","choices":[{"index":0,"delta":{"content":"HbA1c 8.2% on 2026-09-02. "},"finish_reason":null}]}

event: interception
data: {"type":"sentence.verified","sentence_index":1,"doc_char_start":38,"doc_char_end":63,"claim_ids":
["clm_00000000a002"],"risk_severity":"SAFE","action":"RELEASED"}

event: message
data: {"id":"chatcmpl-synth-0002","object":"chat.completion.chunk","created":1792454400,"model":"clinical-summarizer-
v1","choices":[{"index":0,"delta":{"content":"[REDACTED: CRITICAL]"},"finish_reason":null}]}

event: interception
data: {"type":"sentence.verified","sentence_index":2,"doc_char_start":64,"doc_char_end":106,"claim_ids":
["clm_00000000a003"],"risk_severity":"CRITICAL","action":"REDACTED"}

event: message
data: {"id":"chatcmpl-synth-0002","object":"chat.completion.chunk","created":1792454400,"model":"clinical-summarizer-
v1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

event: interception
data: {"type":"stream.complete","status":"REDACTED","note_id":"5b1d2f0e-8c4a-4e6b-9f1a-
2d3c4b5a6f70","trace_id":"0c7e5a21-3b9d-4f0e-8a6c-
1d2e3f4a5b6c","overall_risk_severity":"CRITICAL","interception_time_ms":412}

data: [DONE]

4.3 Streaming Rules

         1. A sentence is released only after its verification completes. Text is never sent before its verdict.
         2. If verification of a sentence exceeds max_sentence_wait_ms , that sentence is withheld. It is replaced by

             [WITHHELD: VERIFICATION_TIMEOUT] , and the stream finishes with status REDACTED . The stream does not
             fail open.
         3. If the verifier or the audit writer becomes unavailable mid-stream, the interceptor sends an error
             event (§11.1) with clinical_impact: "BLOCKED" , then data: [DONE] . Content already released stays
             released, and the event tells the client to discard it.
         4. A stream never sends finish_reason: "stop" after an error event.

4.4 Sentence Segmentation

Sentence boundaries are determined by the segmenter in the verification stage. Boundaries are reported
through the sentence.verified offsets, so clients never need to split text themselves.

4.5 Latency Headers for Streaming

             [AMENDED 2026-10-20, see docs/api-changes/2026-10-20-latency-trailers.md. No latency value is sent as an HTTP trailer.]
             On streaming responses, X-Interception-Time-MS and X-Interception-First-Release-MS are not sent. The values are interception_time_ms and first_release_ms in the stream.complete event (first_release_ms is null if no content was released).
             On non-streaming responses, X-Interception-Time-MS is a response header. RateLimit-Limit, RateLimit-Remaining and RateLimit-Reset are always response headers.

5. Decomposition

5.1 POST /v1/interceptor/decompose

Splits a clinical note into atomic claims. Each claim has a source sentence, character offsets relative to the
full note, and the medical entities it mentions.

Scopes: interceptor:decompose · Auth: Bearer + mTLS · Requires: Idempotency-Key

Persists one intercepted_notes row (status RECEIVED ) and one audit_traces row (stage EXTRACT ) when the note is
new. A duplicate note for the same patient returns the existing note_id (DATABASE.md uniqueness on
(patient_pseudo_id, note_sha256) ).

5.1.1 Request Body

Field              Type              Required  Constraints         Description
patient_pseudo_id  string                 Yes  ^pid_[0-9a-f]{64}$
                                                                   Patient the note belongs
encounter_pseudo_id string                No   ^eid_[0-9a-f]{64}$  to.

 note              NoteInput              Yes  --                  Encounter the note
 options           DecomposeOptions       No   --                  belongs to.

NoteInput          Type                                            The note to decompose.
                   string
 Field             string                                          Extraction options.
 source_system     string
 note_type                           Required  Constraints
 text                                     Yes  1-64 chars
                                          Yes  1-64 chars, for example PROGRESS_NOTE
                                          Yes  1-200,000 code points. Must not be empty
                                               after trimming.

DecomposeOptions

Field             Type     Default  Constraints
max_claims        integer  200
                                    1-500. Exceeding it returns 422
include_entities  boolean  true     INTERCEPT_CLAIM_LIMIT_EXCEEDED .

                                    When false , entities is always [] .

5.1.2 Example Request

               {
                  "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                  "encounter_pseudo_id": null,
                  "note": {
                      "source_system": "EHR-SYNTH-01",
                      "note_type": "PROGRESS_NOTE",
                      "text": "Chief complaint: Follow-up for type 2 diabetes. Patient denies known drug allergies. HbA1c 8.2%

on 2026-09-02. Started amoxicillin 500 mg TID for sinusitis. Blood pressure stable."
                  },
                  "options": {
                      "max_claims": 200,
                      "include_entities": true
                  }

               }

5.1.3 Example Response

HTTP 201 Created , with Location: /v1/interceptor/notes/5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70 . Returns 200 OK
instead when the note already exists for this patient.

               {
                  "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                  "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                  "encounter_pseudo_id": null,
                  "note_sha256": "a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1a3f1",
                  "char_length": 177,
                  "claim_count": 5,
                  "decompose_trace_id": "0c7e5a21-3b9d-4f0e-8a6c-1d2e3f4a5b6c",
                  "component_version": "decomposer-1.4.0",
                  "warnings": [],
                  "claims": [
                      {
                          "claim_id": "clm_000000000001",
                          "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                          "source_sentence": "Patient denies known drug allergies.",
                          "source_sentence_start": 48,
                          "source_sentence_end": 85,
                          "claim_text": "Patient denies known drug allergies",
                          "char_start": 48,
                          "char_end": 84,
                          "entities": [
                             { "system": "http://snomed.info/sct", "code": "416098002", "display": "Drug allergy", "surface_text":

"drug allergies", "char_start": 66, "char_end": 80 }
                          ]

                      },
                      {

                          "claim_id": "clm_000000000002",
                          "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                          "source_sentence": "Follow-up for type 2 diabetes.",
                          "source_sentence_start": 17,
                          "source_sentence_end": 47,
                          "claim_text": "Patient has type 2 diabetes",
                          "char_start": 31,
                          "char_end": 46,
                          "entities": [

                             { "system": "http://snomed.info/sct", "code": "44054006", "display": "Diabetes mellitus type 2",
"surface_text": "type 2 diabetes", "char_start": 31, "char_end": 46 }

                          ]
                      },
                      {

                          "claim_id": "clm_000000000003",
                          "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                          "source_sentence": "HbA1c 8.2% on 2026-09-02.",
                          "source_sentence_start": 86,
                          "source_sentence_end": 111,
                          "claim_text": "HbA1c was 8.2% on 2026-09-02",
                          "char_start": 86,
                          "char_end": 110,
                          "entities": [

                             { "system": "http://loinc.org", "code": "4548-4", "display": "Hemoglobin A1c/Hemoglobin.total in
Blood", "surface_text": "HbA1c", "char_start": 86, "char_end": 91 }

                          ]
                      },
                      {

                          "claim_id": "clm_000000000004",
                          "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                          "source_sentence": "Started amoxicillin 500 mg TID for sinusitis.",
                          "source_sentence_start": 112,
                          "source_sentence_end": 154,
                          "claim_text": "Patient was started on amoxicillin 500 mg three times daily",
                          "char_start": 112,
                          "char_end": 139,
                          "entities": [

                             { "system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "723", "display": "Amoxicillin",
"surface_text": "amoxicillin", "char_start": 120, "char_end": 131 }

                          ]

                         },
                         {

                            "claim_id": "clm_000000000005",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "source_sentence": "Blood pressure stable.",
                            "source_sentence_start": 155,
                            "source_sentence_end": 177,
                            "claim_text": "Blood pressure is stable",
                            "char_start": 155,
                            "char_end": 176,
                            "entities": []
                         }
                     ]
                 }

5.1.4 Claim ID Derivation

claim_id is deterministic: "clm_" + first 12 hex chars of SHA-256(note_sha256 + ":" + char_start + ":" + char_end
+ ":" + claim_text) . The same note and span always produce the same ID. This lets verify and audit refer to
claims without a shared session. The IDs in the examples above are sequential for readability, so they are
illustrative and will differ in a real deployment.

6. Verification

6.1 POST /v1/interceptor/verify

Evaluates claims against the patient context in the FHIR bundle. Returns one VerificationResult per claim,
using the NLI classes and the severity policy in DATABASE.md §2.4.
Scopes: interceptor:verify · Auth: Bearer + mTLS
Verification is read-only with respect to the clinical record. It writes one audit_traces row (stage VERIFY ) only
when note_id is provided. Without note_id , nothing is persisted.

6.1.1 Request Body

Field              Type                 Required  Constraints                   Description
patient_pseudo_id  string                    Yes  ^pid_[0-9a-f]{64}$            Must match the Patient
                                             No                                 in the bundle.
note_id            UUID                           --                            If present, the verify
                                             Yes                                trace is linked to this
fhir_bundle        FHIRBundle                Yes  As in §4.1.1                  note.
claims             array of ClaimInput       No   1-500 items; claim_id values  Patient context.
                                                  must be unique                Claims to verify.
policy             InterceptionPolicy             min_confidence_entailment
                   subset                         only                          Threshold override.

ClaimInput

Field       Type                    Required  Constraints
claim_id    string                       Yes  ^clm_[0-9a-f]{12}$
claim_text  string                       Yes  1-512 chars
char_start  integer                      Yes   0
char_end    integer                      Yes  > char_start
entities    array of MedicalEntity       No   Default []

MedicalEntity follows DATABASE.md §2.2. Its system must be one of the three CodeSystem URIs.

6.1.2 Example Request: Allergy Mismatch Claims

                 {
                     "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                     "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                     "fhir_bundle": {
                         "resourceType": "Bundle",
                         "type": "collection",
                         "entry": [
                            { "resource": { "resourceType": "Patient", "id": "pat-0001", "gender": "female", "birthDate": "1968-03-

  14" } },
                            { "resource": { "resourceType": "AllergyIntolerance", "id": "allergy-penicillin-001", "type": "allergy",

  "category": ["medication"], "criticality": "high", "code": { "coding": [{ "system": "http://snomed.info/sct", "code":
  "91936005", "display": "Allergy to penicillin" }] }, "patient": { "reference": "Patient/pat-0001" },
  "verificationStatus": { "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-
  verification", "code": "confirmed" }] }, "clinicalStatus": { "coding": [{ "system":
  "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active" }] } } },

                            { "resource": { "resourceType": "Condition", "id": "cond-t2dm-001", "subject": { "reference":
  "Patient/pat-0001" }, "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "44054006", "display":
  "Diabetes mellitus type 2" }] }, "clinicalStatus": { "coding": [{ "system":
  "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active" }] }, "verificationStatus": { "coding":
  [{ "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed" }] } } },

                            { "resource": { "resourceType": "Observation", "id": "obs-hba1c-001", "status": "final", "code": {
  "coding": [{ "system": "http://loinc.org", "code": "4548-4", "display": "Hemoglobin A1c/Hemoglobin.total in Blood" }]
  }, "subject": { "reference": "Patient/pat-0001" }, "effectiveDateTime": "2026-09-02T08:30:00Z", "valueQuantity": {
  "value": 8.2, "unit": "%" } } }

                         ]
                     },
                     "claims": [

                         {
                            "claim_id": "clm_000000000001",
                            "claim_text": "Patient denies known drug allergies",
                            "char_start": 48,
                            "char_end": 84,
                            "entities": [
                                { "system": "http://snomed.info/sct", "code": "416098002", "display": "Drug allergy", "surface_text":

  "drug allergies", "char_start": 66, "char_end": 80 }
                            ]

                         },
                         {

                            "claim_id": "clm_000000000004",
                            "claim_text": "Patient was started on amoxicillin 500 mg three times daily",
                            "char_start": 112,
                            "char_end": 139,
                            "entities": [

                                { "system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "723", "display": "Amoxicillin",
  "surface_text": "amoxicillin", "char_start": 120, "char_end": 131 }

                            ]
                         }
                     ]
                 }

6.1.3 Example Response

HTTP 200 OK .

                 {
                     "verification_context_sha256": "b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0b7d0",
                     "verifier_version": "nli-verifier-2.1.0+policy-1.0.0",
                     "verify_trace_id": "9a4c1e2f-3b5d-4a6e-8f7b-0c1d2e3f4a5b",
                     "overall_risk_severity": "CRITICAL",
                     "summary": {
                         "total": 2,
                         "by_severity": { "CRITICAL": 2, "WARNING": 0, "SAFE": 0 }
                     },
                     "results": [
                         {
                            "result_id": "3d2c1b0a-9f8e-4d7c-8b6a-5e4f3d2c1b0a",
                            "claim_id": "clm_000000000001",
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "nli_class": "CONTRADICTION",
                            "confidence": 0.96,
                            "risk_severity": "CRITICAL",
                            "risk_rule": "contradiction_high_risk",
                            "verifier_version": "nli-verifier-2.1.0+policy-1.0.0"
                         },
                         {
                            "result_id": "4e3d2c1b-0a9f-4e8d-9c7b-6a5f4e3d2c1b",
                            "claim_id": "clm_000000000004",
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "nli_class": "NEUTRAL",
                            "confidence": 0.88,
                            "risk_severity": "CRITICAL",
                            "risk_rule": "drug_allergy_conflict",
                            "verifier_version": "nli-verifier-2.1.0+policy-1.0.0"
                         }
                     ],
                     "warnings": []

                 }

Clinical note on the second result: the NLI class is NEUTRAL because no medication-order fact exists in the
record. The severity is CRITICAL because rule 1 ( drug_allergy_conflict ) overrides the model's class. nli_class
is never changed by a rule (DATABASE.md §2.4).

6.1.4 Entailed Claims

For the same request with claims clm_000000000002 (T2DM) and clm_000000000003 (HbA1c), the verifier returns
ENTAILMENT with matched_fhir_ref set to Condition/cond-t2dm-001 and Observation/obs-hba1c-001 , confidence at or
above 0.85 , and risk_severity: "SAFE" with risk_rule: "entailed" . An ENTAILMENT result without
matched_fhir_ref is invalid (DATABASE.md §2.4).

7. Audit & Safety Reports

7.1 POST /v1/interceptor/audit

Writes the verification results to the audit log, creates hallucination events for flagged claims, and returns a
structured safety report. Attribution spans are generated from the results when they are not supplied.

Scopes: interceptor:audit:write · Auth: Bearer + mTLS · Requires: Idempotency-Key

Writes to audit_traces (stage ATTRIBUTE ), hallucination_events , and updates intercepted_notes.pipeline_status
and claim_count . If any write fails, the entire operation is rolled back and the response is 503
AUDIT_WRITE_FAILED . The interceptor never releases content that is not audited (A3).

7.1.1 Request Body

Field                      Type                 Required  Constraints                 Description
note_id                    UUID                      Yes  Must exist, and belong      Note from decompose.
                                                     Yes  to patient_pseudo_id
patient_pseudo_id          string                    No   ^pid_[0-9a-f]{64}$          Must match the note's
                                                     Yes                              patient.
verify_trace_ids           array of UUID             Yes  0-20 items                  Verify traces this report is
                                                                                      based on.
claims                     array of                  No   1-500 items; must exist     As in DATABASE.md §2.3.
results                    AtomicProposition              for note_id
                                                     No   One per claim;              As in DATABASE.md §2.4.
                           array of                       claim_id values must
                           VerificationResult             match claims exactly        If omitted, the server
                                                          0-1000 items                derives spans from the
attribution                array of                                                   results.
                           AttributionSpan                Default ["CRITICAL",        Severities that produce a
                                                          "WARNING"]                  hallucination_events
options.create_events_for  array of "CRITICAL"                                        row. SAFE is always
                           | "WARNING"                                                excluded (DATABASE.md
                                                                                      §3.4).

Each claim_id in results must appear in claims . Any mismatch returns 422 AUDIT_CLAIM_MISMATCH .

7.1.2 Hallucination Category Mapping

[AMENDED 2026-10-20, see docs/api-changes/2026-10-20-category-dose-mismatch.md. A dose or frequency on a drug in an allergy class is MEDICATION_DOSE_MISMATCH. A matched allergy still gives DRUG_ALLERGY_CONFLICT.]

The category is set by the server from the rule and the entity domain. Clients do not choose categories.

Condition (evaluated in order)                                                        category
risk_rule = drug_allergy_conflict , or a CONTRADICTION where an entity is an allergy concept  DRUG_ALLERGY_CONFLICT
CONTRADICTION where an entity is a medication with a dose or frequency                MEDICATION_DOSE_MISMATCH
CONTRADICTION where an entity is a drug in an allergy class, no dose or frequency     DRUG_ALLERGY_CONFLICT
CONTRADICTION where an entity is an active diagnosis                                  DIAGNOSIS_CONTRADICTION
CONTRADICTION or NEUTRAL where the matched fact is an Observation                     LAB_VALUE_MISMATCH
Claim references a date inconsistent with its matched fact                            TEMPORAL_MISMATCH
risk_rule = unsupported_clinical                                                      UNSUPPORTED_CLAIM
Anything else                                                                         OTHER

7.1.3 Example Request

                 {
                     "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                     "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                     "verify_trace_ids": ["9a4c1e2f-3b5d-4a6e-8f7b-0c1d2e3f4a5b"],
                     "claims": [
                         {
                            "claim_id": "clm_000000000001",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "source_sentence": "Patient denies known drug allergies.",
                            "source_sentence_start": 48,
                            "source_sentence_end": 85,
                            "claim_text": "Patient denies known drug allergies",
                            "char_start": 48,
                            "char_end": 84,
                            "entities": [
                                { "system": "http://snomed.info/sct", "code": "416098002", "display": "Drug allergy", "surface_text":

  "drug allergies", "char_start": 66, "char_end": 80 }
                            ]

                         },
                         {

                            "claim_id": "clm_000000000004",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "source_sentence": "Started amoxicillin 500 mg TID for sinusitis.",
                            "source_sentence_start": 112,
                            "source_sentence_end": 154,
                            "claim_text": "Patient was started on amoxicillin 500 mg three times daily",
                            "char_start": 112,
                            "char_end": 139,
                            "entities": [

                                { "system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "723", "display": "Amoxicillin",
  "surface_text": "amoxicillin", "char_start": 120, "char_end": 131 }

                            ]
                         }
                     ],
                     "results": [
                         {

                            "result_id": "3d2c1b0a-9f8e-4d7c-8b6a-5e4f3d2c1b0a",
                            "claim_id": "clm_000000000001",
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "nli_class": "CONTRADICTION",
                            "confidence": 0.96,
                            "risk_severity": "CRITICAL",
                            "risk_rule": "contradiction_high_risk",
                            "verifier_version": "nli-verifier-2.1.0+policy-1.0.0"
                         },
                         {
                            "result_id": "4e3d2c1b-0a9f-4e8d-9c7b-6a5f4e3d2c1b",
                            "claim_id": "clm_000000000004",
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "nli_class": "NEUTRAL",
                            "confidence": 0.88,
                            "risk_severity": "CRITICAL",
                            "risk_rule": "drug_allergy_conflict",
                            "verifier_version": "nli-verifier-2.1.0+policy-1.0.0"
                         }
                     ],
                     "attribution": [
                         {
                            "span_id": "6f5e4d3c-2b1a-4f9e-8d7c-0b1a2f3e4d5c",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "doc_char_start": 48,
                            "doc_char_end": 84,
                            "classification": "SOURCE_CONTRADICTED",

                            "claim_id": "clm_000000000001",
                            "result_id": "3d2c1b0a-9f8e-4d7c-8b6a-5e4f3d2c1b0a",
                            "explanation": "Note states no known drug allergies. The record documents a confirmed allergy to
  penicillin."
                         },
                         {
                            "span_id": "7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "doc_char_start": 112,
                            "doc_char_end": 139,
                            "classification": "SOURCE_CONTRADICTED",
                            "claim_id": "clm_000000000004",
                            "result_id": "4e3d2c1b-0a9f-4e8d-9c7b-6a5f4e3d2c1b",
                            "explanation": "Amoxicillin is a penicillin-class antibiotic. The record documents a confirmed
  penicillin allergy."
                         }
                     ],
                     "options": {
                         "create_events_for": ["CRITICAL", "WARNING"]
                     }
                 }

The example shows 2 of 5 claims for brevity. A real request must contain one result per claim (DATABASE.md
§4.5, S5).

7.1.4 Example Response

HTTP 201 Created , with Location: /v1/interceptor/audit/reports/7e2a9b4c-1f3d-4a8e-b5c6-9d0e1f2a3b4c . Returns
200 OK with idempotent_replay: true when the same Idempotency-Key and body are replayed.

                 {
                    "report_id": "7e2a9b4c-1f3d-4a8e-b5c6-9d0e1f2a3b4c",
                    "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                    "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
                    "overall_risk_severity": "CRITICAL",
                    "pipeline_status": "BLOCKED",
                    "counts": { "CRITICAL": 2, "WARNING": 0, "SAFE": 0 },
                    "hallucination_events": [
                        {
                            "event_id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                            "claim_id": "clm_000000000001",
                            "category": "DRUG_ALLERGY_CONFLICT",
                            "risk_severity": "CRITICAL",
                            "nli_class": "CONTRADICTION",
                            "risk_rule": "contradiction_high_risk",
                            "confidence": 0.960,
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "detected_at": "2026-10-19T16:40:12Z"
                        },
                        {
                            "event_id": "e2f3a4b5-d6e7-4f8a-9b0c-1d2e3f4a5b6c",
                            "claim_id": "clm_000000000004",
                            "category": "DRUG_ALLERGY_CONFLICT",
                            "risk_severity": "CRITICAL",
                            "nli_class": "NEUTRAL",
                            "risk_rule": "drug_allergy_conflict",
                            "confidence": 0.880,
                            "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001",
                            "detected_at": "2026-10-19T16:40:12Z"
                        }
                    ],
                    "attribution_spans": [
                        {
                            "span_id": "6f5e4d3c-2b1a-4f9e-8d7c-0b1a2f3e4d5c",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "doc_char_start": 48,
                            "doc_char_end": 84,
                            "classification": "SOURCE_CONTRADICTED",
                            "claim_id": "clm_000000000001",
                            "result_id": "3d2c1b0a-9f8e-4d7c-8b6a-5e4f3d2c1b0a",
                            "explanation": "Note states no known drug allergies. The record documents a confirmed allergy to

  penicillin."
                        },
                        {
                            "span_id": "7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d",
                            "note_id": "5b1d2f0e-8c4a-4e6b-9f1a-2d3c4b5a6f70",
                            "doc_char_start": 112,
                            "doc_char_end": 139,
                            "classification": "SOURCE_CONTRADICTED",
                            "claim_id": "clm_000000000004",
                            "result_id": "4e3d2c1b-0a9f-4e8d-9c7b-6a5f4e3d2c1b",
                            "explanation": "Amoxicillin is a penicillin-class antibiotic. The record documents a confirmed

  penicillin allergy."
                        }

                    ],
                    "trace_id": "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e",
                    "created_at": "2026-10-19T16:40:12Z",
                    "idempotent_replay": false
                 }

pipeline_status on the note becomes BLOCKED because at least one CRITICAL event was produced.
overall_risk_severity is the highest severity among the results: CRITICAL > WARNING > SAFE .

7.2 Supporting Read Endpoints

Method & path                                      Scope             Returns
GET /v1/interceptor/notes/{note_id}                interceptor:read
                                                                     Note metadata
GET /v1/interceptor/audit/reports/{report_id}      interceptor:read  ( intercepted_notes row) and its
                                                                     claims, without raw note text.

                                                                     The report from §7.1.4, identical in
                                                                     shape.

Both endpoints return 404 NOTE_NOT_FOUND or REPORT_NOT_FOUND for unknown IDs. They also return 404 when the
requesting client is not authorized for the patient, so the existence of a record is not revealed.

8. Data & Persistence Rules

8.1 Pseudonymization

             Patient.identifier values are removed on receipt. They are never logged, traced, stored, or returned.
             patient_pseudo_id is the only patient key in any request, response, log line, or table.
             Request bodies are not logged. Only metadata (endpoint, status, latency, IDs) is logged.

8.2 Note Text

             Raw note text is written to the encrypted object store, not PostgreSQL (DATABASE.md A5).
             Responses for decompose and verify return offsets and claim_text . They do not echo the full note.
             source_sentence is returned in decompose and audit responses. It is limited to the sentence containing
             the claim.

8.3 Idempotency

Endpoint                        Key scope                     Replay behavior
                                (client_id, Idempotency-Key)
POST                                                          Same key and same body: returns the original
/v1/interceptor/decompose                                     response with 200 . Same key and different body: 409
                                                              IDEMPOTENCY_KEY_REUSED .
POST /v1/interceptor/audit      (client_id, Idempotency-Key)
                                                              As above. Replays never write a second set of
POST                            Not idempotent. The upstream  events.
/v1/chat/completions/intercept  model is not deterministic.
                                                              Clients that retry must treat each call as a new
POST /v1/interceptor/verify     Naturally idempotent (read-   interception.
                                only).
                                                              --

Idempotency keys expire 24 hours after first use.

9. Latency Targets

These are proposed targets (A5). Each is measured server-side and reported in X-Interception-Time-MS .

Operation                           Target p95     Measured as
                                           800 ms  Request receipt to response
decompose , note  2 KB                     250 ms  Request receipt to response
verify ,  20 claims                        150 ms  Request receipt to commit
                                           400 ms  Sentence complete to verdict
audit ,  20 claims
                                    1200 ms Request receipt to first RELEASED content
Proxy overhead per sentence
(excluding upstream model time)
Proxy time to first released chunk
(excluding upstream model time)

X-Interception-Time-MS measures interception time only. It excludes upstream model latency. Upstream
latency is reported separately in X-Upstream-Time-MS on the proxy endpoint.

10. HTTP Status Codes               Used for

 Status                             Successful read, verify, proxy response (including BLOCKED in
  200 OK                            §4.1.4), idempotent replay.
                                    New note from decompose , new report from audit . Includes
  201 Created                       Location .

  400 Bad Request                   Malformed JSON, or a body that fails the JSON schema.
  401 Unauthorized
  403 Forbidden                     Missing, expired, or invalid bearer token.

  404 Not Found                     Valid token but missing scope, or missing or unregistered mTLS
                                    client certificate.
  409 Conflict
  413 Payload Too Large             Unknown note_id or report_id , or a resource the caller is not
  415 Unsupported Media Type        authorized to see.
  422 Unprocessable Entity          Idempotency key reused with a different body.

  429 Too Many Requests             Body, note text, or bundle exceeds its limit.
  500 Internal Server Error
  502 Bad Gateway                   Content-Type is not application/json .
  503 Service Unavailable
  504 Gateway Timeout               Syntactically valid but clinically or semantically invalid: unresolved
                                    references, patient mismatch, claim-result mismatch.
                                    Rate limit exceeded. Retry-After is set.

                                    Unexpected interceptor fault. Content is not released.

                                    Upstream model returned an error. Content is not released.

                                    Verifier or audit writer unavailable (fail-closed). Retry-After is set.

                                    Upstream model did not respond within the timeout. Content is not
                                    released.

11. Clinical Error Payloads

11.1 Error Object

All errors (except SSE error events, which use the same object) return this structure:

                 {
                     "error": {
                         "type": "clinical_interception_error",
                         "code": "INTERCEPT_PATIENT_MISMATCH",
                         "message": "The patient in fhir_bundle does not match clinical_context.patient_pseudo_id.",
                         "param": "clinical_context.patient_pseudo_id",
                         "clinical_impact": "BLOCKED",
                         "fail_mode": "CLOSED",
                         "retryable": false,
                         "request_id": "2f9e8d7c-6b5a-4c3d-9e2f-1a0b9c8d7e6f",
                         "trace_id": null,
                         "docs_url": "https://docs.example-hospital.internal/interceptor/errors#INTERCEPT_PATIENT_MISMATCH"
                     }

                 }

 Field                Type                   Description
  type                string                 Always clinical_interception_error .
  code                string                 Stable machine-readable code from §11.2.
  message             string                 Human-readable. Must not contain patient identifiers or note
                                             text.
  param               string | null          JSON path of the offending field.
  clinical_impact     "NONE" | "DEGRADED" |  BLOCKED : no clinical content was released. DEGRADED : the
                      "BLOCKED"              request completed with reduced checking, and the response
  fail_mode                                  says what was skipped. NONE : no effect on clinical content.
  retryable           "CLOSED"               Always CLOSED in v1 (A3).
  request_id          boolean                Whether the same request may succeed if retried unchanged.
  trace_id            string                 Echo of X-Request-Id .
  docs_url            UUID | null            Set when an audit trace was written before the failure.
                      string                 Link to the error's documentation anchor.
11.2 Error Codes
                      HTTP    clinical_impact  retryable  Meaning
 Code                    400  NONE                  No
  VALIDATION_FAILED                                       Body fails the JSON
                      401 NONE                      No    schema. param names
  AUTH_TOKEN_INVALID                                      the field.
                      403 NONE                      No
  AUTH_MTLS_REQUIRED                                      Bearer token missing,
                                                          expired, or failed
                                                          signature check.

                                                          Clinical endpoint called
                                                          without a registered
                                                          client certificate.

Code                                 HTTP    clinical_impact  retryable  Meaning
AUTH_SCOPE_INSUFFICIENT                 403  NONE                  No
NOTE_NOT_FOUND                                                     No    Token lacks the scope
REPORT_NOT_FOUND                     404 NONE                      No    for this endpoint.
IDEMPOTENCY_KEY_REUSED                                             No
                                     404 NONE                      No    note_id unknown or
INTERCEPT_NOTE_TOO_LARGE                                           No    not visible to the caller.
INTERCEPT_BUNDLE_TOO_LARGE           409 NONE                      No
UNSUPPORTED_MEDIA_TYPE                                             No    report_id unknown or
INTERCEPT_BUNDLE_INVALID             413 BLOCKED                         not visible to the caller.
                                     413 BLOCKED                   No
                                     415 NONE                            Same Idempotency-
                                     422 BLOCKED                   No    Key used with a
                                                                   No    different body.
INTERCEPT_FHIR_REFERENCE_UNRESOLVED  422 BLOCKED                   No
                                                                         Note text exceeds
INTERCEPT_PATIENT_MISMATCH           422 BLOCKED                   No    200,000 code points.
INTERCEPT_CLAIM_LIMIT_EXCEEDED       422 BLOCKED                   Yes
AUDIT_CLAIM_MISMATCH                 422 BLOCKED                   Yes   Bundle exceeds 500
                                                                   Yes   entries or 1.5 MB.
AUDIT_NOTE_PATIENT_MISMATCH          422 BLOCKED                   Yes
RATE_LIMITED                         429 NONE                            Content-Type is not
INTERCEPT_INTERNAL_ERROR             500 BLOCKED                         application/json .
INTERCEPT_UPSTREAM_ERROR             502 BLOCKED
INTERCEPT_UPSTREAM_TIMEOUT           504 BLOCKED                         Bundle is not
                                                                         collection , or a
                                                                         resource fails
                                                                         DATABASE.md §1
                                                                         validation.

                                                                         A subject or patient
                                                                         reference does not
                                                                         match a resource in
                                                                         the bundle (ingestion
                                                                         rule R1).

                                                                         Bundle patient does
                                                                         not match
                                                                         patient_pseudo_id .

                                                                         More claims than
                                                                         max_claims .

                                                                         A results entry has a
                                                                         claim_id not in
                                                                         claims , or a claim has
                                                                         no result.

                                                                         patient_pseudo_id
                                                                         does not match the
                                                                         note's patient.

                                                                         Rate limit exceeded.
                                                                         See Retry-After .

                                                                         Unhandled interceptor
                                                                         fault.

                                                                         Upstream model
                                                                         returned an error.

                                                                         Upstream model timed
                                                                         out.

Code                            HTTP              clinical_impact  retryable  Meaning
INTERCEPT_VERIFIER_UNAVAILABLE     503            BLOCKED               Yes
                                                                              NLI verifier
AUDIT_WRITE_FAILED              503 BLOCKED                             Yes   unavailable. Fail-
                                                                              closed.
INTERCEPT_SENTENCE_TIMEOUT      503 DEGRADED                            Yes
                                                                              Audit log unavailable.
                                                                              Fail-closed: nothing is
                                                                              released (A3).

                                                                              A sentence exceeded
                                                                              max_sentence_wait_ms
                                                                              and was withheld.
                                                                              Used in SSE error
                                                                              events only when the
                                                                              stream cannot
                                                                              continue.

Warnings, not errors. Conditions that do not stop processing are returned in the warnings array of a
successful response, not as errors. Examples: INTERCEPT_UNMAPPED_CODE (DATABASE.md ingestion rule R3),
INTERCEPT_UNSUPPORTED_RESOURCE (an ignored resource type). Each warning is an object with code , message , and
param .

11.3 Example: Fail-Closed Verifier Outage

HTTP 503 Service Unavailable , Retry-After: 30 :

{
   "error": {
       "type": "clinical_interception_error",
       "code": "INTERCEPT_VERIFIER_UNAVAILABLE",
       "message": "The verification service is not responding. No content was released.",
       "param": null,
       "clinical_impact": "BLOCKED",
       "fail_mode": "CLOSED",
       "retryable": true,
       "request_id": "8c7b6a5f-4e3d-4c2b-9a1f-0e9d8c7b6a5f",
       "trace_id": "0c7e5a21-3b9d-4f0e-8a6c-1d2e3f4a5b6c",
       "docs_url": "https://docs.example-hospital.internal/interceptor/errors#INTERCEPT_VERIFIER_UNAVAILABLE"
   }

}

12. Health Endpoints

Method & path                                     Auth                     Returns
GET /v1/health                                    None (internal LB only)
                                                                           200 with {"status":"ok"} when
GET /v1/health/ready                              None (internal LB only)  the process is alive.

                                                                           200 when the verifier, audit
                                                                           database, and upstream routing are
                                                                           reachable. 503 otherwise, with a
                                                                           components object listing each
                                                                           dependency's status.

Readiness failures remove the instance from the load balancer. They do not change the fail-closed behavior of
in-flight requests.

13. Request Limits

Limit                                          Value            Error on breach
Request body                                    2 MiB           413 INTERCEPT_BUNDLE_TOO_LARGE or 413
                                                                INTERCEPT_NOTE_TOO_LARGE
Note text                 200,000 code points                   413 INTERCEPT_NOTE_TOO_LARGE
FHIR bundle entries                                500          413 INTERCEPT_BUNDLE_TOO_LARGE
Claims per request                                 500          422 INTERCEPT_CLAIM_LIMIT_EXCEEDED
Messages per proxy                                 100          400 VALIDATION_FAILED
request
Requests per client       Per §0 open items; default            429 RATE_LIMITED
                                            60 per minute
Upstream call timeout                                           504 INTERCEPT_UPSTREAM_TIMEOUT
Verification per request                                 120 s  503 INTERCEPT_VERIFIER_UNAVAILABLE

                                                   30 s total

14. Compliance Checklist

#   Requirement                                                 Where enforced

C1  Both bearer token and mTLS are required on all §2.1

    clinical endpoints

C2  No content is released without an audit record              §7.1, §11.2 AUDIT_WRITE_FAILED

C3  No fail-open path exists                                    A3, §11.1 fail_mode

C4  Patient identifiers are never logged or returned            §8.1

C5  Hallucination categories are set by the server,             §7.1.2

    not the client

C6  ENTAILMENT always carries a matched FHIR                    DATABASE.md §2.4

    reference

C7  Severity overrides are recorded in risk_rule                DATABASE.md §2.4

C8  Audit records are append-only                               DATABASE.md §3.3-3.4

C9  Offsets are code points, not bytes                          §1.4

````
