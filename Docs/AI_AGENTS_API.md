# AI Agents API — contract

**Status: DRAFT for review.** Nothing here is deployed yet.

This is the interface between the AI workstream and the application. Lloyd builds the UI
against this; Zuhair implements behind it. If something here is awkward for the frontend, say
so now — it is cheaper to change this document than the implementation.

Covers the SOW "AI and Data Management" line item:
*"API endpoints for triggering AI analysis and retrieving results for each agent capability."*

Backing data model: [`Backend/sql/001_ai_schema.sql`](../Backend/sql/001_ai_schema.sql).

---

## 1. Why this is asynchronous

Every route on the existing HTTP API is capped at **29 seconds**
(`timeoutMilliseconds: 29000` in `IaC/9_apigateway/config/prod.yaml`, and API Gateway's hard
ceiling is 30s).

A single contract goes through BDA extraction plus up to three Bedrock agent calls. That will
routinely exceed 29 seconds. So analysis **cannot** be request/response — the API starts work
and the client reads results later.

This fits the SOW, which puts live refresh out of scope: *"The dashboard displays data as of
page load... Users refresh the page to see updated information."* So no websockets and no
continuous polling — the app reads status when a page loads.

---

## 2. Conventions

| | |
|---|---|
| Base URL | `https://6eqsnokjn9.execute-api.us-west-2.amazonaws.com` |
| Auth | Cognito JWT — `Authorization: Bearer <id_token>` (same authorizer as `/upload`, `/chat`) |
| Case | **JSON is camelCase. The database is snake_case.** Mapping tables below are authoritative. |
| Timestamps | ISO 8601, UTC, e.g. `2026-09-16T15:15:48Z` |
| IDs | UUID strings |

> The camelCase/snake_case split is deliberate but it is exactly the kind of mismatch that
> silently broke `memory_size` in the Lambda config. Every field is mapped explicitly below —
> please don't infer the mapping.

---

## 3. The happy path (no trigger call needed)

Analysis starts **automatically on upload**. Lloyd does not have to call anything to kick it off.

```
1. App uploads document          POST /upload              (existing route)
2. S3 event  →  BDA extraction                             (automatic)
3. Extraction completes  →  the 3 pre-signing agents run   (automatic)
4. App reads status on page load GET  /contracts/{id}/analysis
5. App reads results             GET  /contracts/{id}/analysis/{agentType}
```

This matches SOW acceptance criterion #1 — *"Contracts uploaded to the system... are analyzed
by AI agents"* — without the app having to orchestrate anything.

The explicit `POST` in §4.1 exists only for **re-running** analysis (for example after a
document is re-uploaded following legal review).

---

## 4. Endpoints

### 4.1 Trigger / re-run analysis

```http
POST /contracts/{contractId}/analysis
```

Request body is optional. Omit it to run all three pre-signing agents.

```json
{ "agents": ["riskClause", "templatePrepopulation", "summary"] }
```

**202 Accepted**

```json
{
  "contractId": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "extractionId": "b1e1c0de-1f2a-4c3d-9e8f-0a1b2c3d4e5f",
  "runs": [
    { "runId": "1a2b3c4d-...", "agentType": "riskClause",            "status": "pending" },
    { "runId": "2b3c4d5e-...", "agentType": "templatePrepopulation", "status": "pending" },
    { "runId": "3c4d5e6f-...", "agentType": "summary",               "status": "pending" }
  ]
}
```

| Code | When |
|---|---|
| `202` | Accepted, runs created |
| `404` | Unknown `contractId` |
| `409` | Analysis already running for this contract — returns the in-flight runs |
| `422` | Document has no successful extraction and cannot be analyzed |

### 4.2 Status — poll this on page load

```http
GET /contracts/{contractId}/analysis
```

**200 OK**

```json
{
  "contractId": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "extraction": {
    "status": "succeeded",
    "pageCount": 12,
    "completedAt": "2026-09-16T15:10:02Z"
  },
  "agents": {
    "riskClause":            { "runId": "1a2b3c4d-...", "status": "succeeded", "completedAt": "2026-09-16T15:11:20Z", "resultCount": 7 },
    "templatePrepopulation": { "runId": "2b3c4d5e-...", "status": "succeeded", "completedAt": "2026-09-16T15:11:44Z", "resultCount": 14 },
    "summary":               { "runId": "3c4d5e6f-...", "status": "running",   "completedAt": null, "resultCount": null },
    "obligationTracking":    { "runId": null,           "status": "notStarted", "completedAt": null, "resultCount": null }
  }
}
```

`status` is one of `notStarted`, `pending`, `running`, `succeeded`, `failed`.
When `failed`, an `error` string is included on that agent.

`resultCount` lets the UI render counts (e.g. "7 risks flagged") without fetching full results.

### 4.3 Results per agent

```http
GET /contracts/{contractId}/analysis/{agentType}
```

`agentType` is one of `risk-clause`, `template-prepopulation`, `summary`,
`obligation-tracking` (kebab-case in the URL).

Returns the **most recent succeeded run**. Add `?runId=<uuid>` to fetch a specific historical run.

If the agent has not succeeded yet, returns `409` with the current status rather than partial data.

---

## 5. Response shapes

### 5.1 `risk-clause` — Agent 1, pre-signing

```json
{
  "runId": "1a2b3c4d-...",
  "agentType": "riskClause",
  "status": "succeeded",
  "completedAt": "2026-09-16T15:11:20Z",
  "risks": [
    {
      "id": "9f8e7d6c-...",
      "category": "missingClause",
      "severity": "high",
      "title": "No background investigation exhibit attached",
      "detail": "Supplier personnel will have unescorted facility access, but the agreement does not attach the Background Investigation Exhibit.",
      "playbookSection": "access_to_facilities",
      "suggestedLanguage": "Access to Facilities. To the extent supplier performs Services at [company] facilities, supplier shall: ...",
      "sourceQuote": "Supplier personnel may require badge access to Company premises.",
      "sourcePage": 3
    }
  ]
}
```

| JSON | DB column (`judy_ai.contract_risks`) | Notes |
|---|---|---|
| `id` | `id` | |
| `category` | `risk_category` | `unusualTerm` \| `missingClause` \| `dateMismatch` \| `complianceGap` \| `trackedTerm` — the SOW's four finding types, plus `trackedTerm` for any term carrying a date, deadline or figure a reviewer must see before signing (renewal windows, notice periods, commission steps, monetary thresholds). These are informational, not necessarily problems — render them distinctly from the risk categories. See `Backend/sql/002_risk_category_tracked_term.sql` |
| `severity` | `severity` | `high` \| `medium` \| `low` |
| `title` | `title` | Short label for a dashboard row |
| `detail` | `detail` | What is wrong and why |
| `playbookSection` | `playbook_section` | Which playbook rule fired. `null` for general-pattern findings |
| `suggestedLanguage` | `suggested_language` | **Advisory only — never applied to the document.** `null` if none |
| `sourceQuote` | `source_quote` | The sentence that triggered the flag |
| `sourcePage` | `source_page` | |

> `suggestedLanguage` is the SOW's "AI generated suggestions for alternative language."
> The UI must present it as a suggestion. It must not auto-edit the source document.

### 5.2 `template-prepopulation` — Agent 2, pre-signing

```json
{
  "runId": "2b3c4d5e-...",
  "agentType": "templatePrepopulation",
  "status": "succeeded",
  "completedAt": "2026-09-16T15:11:44Z",
  "detectedTemplateType": "msa",
  "rationale": "Document defines master terms with reference to future statements of work.",
  "fields": [
    {
      "id": "4d5e6f70-...",
      "fieldType": "signature",
      "label": "Supplier authorised signatory",
      "signerRole": "supplier",
      "page": 11,
      "position": { "x": 0.12, "y": 0.64, "width": 0.30, "height": 0.05 },
      "isRequired": true
    }
  ]
}
```

| JSON | DB column | Notes |
|---|---|---|
| `detectedTemplateType` | `template_detections.detected_template_type` | `sow` \| `purchaseAgreement` \| `msa` \| `unknown` — **confirmed by Eean 2026-09-16** (MSA, PA, SOW; change orders hang off SOWs) |
| `rationale` | `template_detections.rationale` | Why it classified that way |
| `fields[].fieldType` | `contract_fields.field_type` | `signature` \| `initial` \| `date` \| `fullName` \| `title` \| `company` \| `text` |
| `fields[].label` | `contract_fields.label` | What the document calls it |
| `fields[].signerRole` | `contract_fields.signer_role` | e.g. `supplier`, `company`, `witness` |
| `fields[].page` | `contract_fields.page` | 1-indexed |
| `fields[].position` | `contract_fields.position` | Normalized 0–1 page coordinates, origin top-left |
| `fields[].isRequired` | `contract_fields.is_required` | |

> `position` is normalized so the UI can overlay a box at any zoom level without knowing the
> rendered page size. **Confirmed with Lloyd 2026-09-18** — normalized 0–1, origin top-left,
> resolution-independent. Locked; changing it now means changing both sides.

### 5.3 `summary` — Agent 3, pre-signing

```json
{
  "runId": "3c4d5e6f-...",
  "agentType": "summary",
  "status": "succeeded",
  "completedAt": "2026-09-16T15:12:05Z",
  "summaryText": "This is a master services agreement between ...",
  "keyPoints": [
    "Initial term of three years with automatic annual renewal.",
    "Supplier may not subcontract without written approval."
  ],
  "wordCount": 180
}
```

| JSON | DB column (`judy_ai.contract_summaries`) |
|---|---|
| `summaryText` | `summary_text` |
| `keyPoints` | `key_points` (JSONB array of strings) |
| `wordCount` | `word_count` |

### 5.4 `obligation-tracking` — Agent 4, post-signing

Not available until the contract is signed. Returns `409` before then.

```json
{
  "runId": "5e6f7081-...",
  "agentType": "obligationTracking",
  "status": "succeeded",
  "completedAt": "2026-09-20T09:03:11Z",
  "obligations": [
    {
      "id": "6f708192-...",
      "obligationType": "renewal",
      "description": "Agreement renews automatically unless cancelled.",
      "ownerParty": "company",
      "dueDate": "2027-09-19",
      "rawDateText": "sixty (60) days prior to the anniversary of the Effective Date",
      "recurrence": "annual",
      "sourceQuote": "This Agreement shall renew for successive one-year terms unless either party gives notice...",
      "sourcePage": 7
    }
  ]
}
```

| JSON | DB column (`judy_ai.contract_obligations`) | Notes |
|---|---|---|
| `obligationType` | `obligation_type` | `commitment` \| `renewal` \| `milestone` \| `other` |
| `ownerParty` | `owner_party` | Who owes it |
| `dueDate` | `due_date` | **Can be `null`** — the SOW scopes this to obligations "where clearly stated" |
| `rawDateText` | `raw_date_text` | What the document literally said. Show this when `dueDate` is `null` |
| `recurrence` | `recurrence` | `oneTime` \| `monthly` \| `quarterly` \| `annual`, or `null` |

> `dueDate` and `rawDateText` are a pair. When a contract says *"within 30 days of the Effective
> Date"*, there may be no resolvable calendar date, but the wording still has to reach the user.
> **Confirmed with Lloyd 2026-09-18**: `dueDate` is nullable and the UI falls back to
> `rawDateText` for relative wording. Locked.

---

## 6. Errors

```json
{
  "error": {
    "code": "ANALYSIS_NOT_READY",
    "message": "Summary agent has not completed for this contract.",
    "status": "running"
  }
}
```

| Code | HTTP | Meaning |
|---|---|---|
| `CONTRACT_NOT_FOUND` | 404 | Unknown `contractId` |
| `ANALYSIS_NOT_READY` | 409 | Agent has not succeeded yet — `status` tells you where it is |
| `ANALYSIS_IN_PROGRESS` | 409 | Re-run requested while one is already running |
| `EXTRACTION_FAILED` | 422 | BDA could not read the document. Per the SOW these go to the DLQ for manual resolution |
| `UNAUTHORIZED` | 401 | Missing or invalid Cognito token |

---

## 7. Lambda functions behind this

Workers are split from the API handler so that no HTTP request can hit the 29-second ceiling.

| Function | Role | Invoked by |
|---|---|---|
| `analysis-api` | Serves every route in §4. Only reads/writes Postgres and fires async invokes. Always fast. | API Gateway |
| `document-extraction` | BDA invocation, text normalization, writes `judy_ai.document_extractions` | S3 event on upload |
| `agent-risk-clause` | Agent 1 | `document-extraction` on success, or `analysis-api` re-run |
| `agent-template-prepopulation` | Agent 2 | same |
| `agent-summary` | Agent 3 | same |
| `agent-obligation-tracking` | Agent 4 | signature completion event |

---

## 8. Routes to add — drop-in for `IaC/9_apigateway/config/prod.yaml`

Andres — these follow the existing `lambdaFunctions` pattern exactly.

```yaml
      analysisTriggerPost:
        functionName: "analysis-api"
        path: "/contracts/{contractId}/analysis"
        method: "POST"
        integrationMethod: "POST"
        authRequired: true
        timeoutMilliseconds: 29000
      analysisStatusGet:
        functionName: "analysis-api"
        path: "/contracts/{contractId}/analysis"
        method: "GET"
        integrationMethod: "POST"
        authRequired: true
        timeoutMilliseconds: 29000
      analysisResultsGet:
        functionName: "analysis-api"
        path: "/contracts/{contractId}/analysis/{agentType}"
        method: "GET"
        integrationMethod: "POST"
        authRequired: true
        timeoutMilliseconds: 29000
```

`analysis-api` will also need `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue` for
the Aurora secret, and `lambda:InvokeFunction` on the four agent workers. Following the existing
SSM pattern (`/rag-app/kms/dynamodb-arn`), I'd suggest publishing the Aurora cluster ARN and
secret ARN as SSM parameters from `2_data` so `8_lambda` can consume them.

---

## 9. Open items

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Contract types `sow` / `purchaseAgreement` / `msa` | Eean | ✅ Confirmed 2026-09-16 |
| 2 | Who writes `app.contracts` on upload, and which fields the upload flow needs | Lloyd | Open |
| 3 | `position` normalized vs absolute pixels | Lloyd | ✅ Normalized, confirmed 2026-09-18 |
| 4 | How signature completion signals Agent 4 (event shape / source) | Lloyd + Zuhair | Open |
| 5 | Postgres vs DynamoDB for AI results | Andres | ✅ Postgres — schema applied 2026-09-17 |
| 6 | Whether these routes live on the existing `rag-app-prod-api` or a separate API | Andres | Open |
| 7 | `dueDate` nullable with `rawDateText` fallback | Lloyd | ✅ Confirmed 2026-09-18 |
