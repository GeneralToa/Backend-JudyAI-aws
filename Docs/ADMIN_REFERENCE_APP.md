# Judy AI — Administrator Reference: Application and Signing Workflow

| | |
|---|---|
| Scope | The application workstream: contract management, signature workflow, admin panel, and frontend |
| Audience | Administrators who deploy, configure and operate the Judy AI environment |
| Environment | AWS account `580118073904`, region `us-west-2`, resource prefix `rag-app-prod` |
| Related | Infrastructure chapter (`TECHNICAL_DOCUMENTATION.md`); AI chapter (`ADMIN_REFERENCE_AI.md`); AI API contract (`AI_AGENTS_API.md`) |

This chapter is part of the single administrator reference document the SOW requires
(acceptance criterion 12). It covers the application layer only: the signing workflow, admin
panel, frontend, and the Lambda functions and database tables that support them.

---

## 1. What the application does

Judy AI manages the full contract lifecycle from upload to signed. When a user uploads a
document, the application creates a contract record and triggers AI analysis. When the contract
is ready, an authorised user sends it for signature by assigning signers from an approval
template. Signers receive email notifications and sign through the web interface. The last
signature closes the contract and triggers obligation extraction.

```
Upload
  └─ app.contracts row created (status: uploaded)
       └─ AI analysis runs (status: analyzing → ready_for_review)

Send for Signature (POST /contracts/{id}/route)
  └─ app.contract_signers rows created
  └─ SES email sent to ALL assigned signers (sequential and parallel alike)
  └─ status: routed_for_signature

Sign (POST /contracts/{id}/sign)
  └─ app.signer_signatures row created
  └─ app.contract_signers status: signed
  └─ if all signed: status: signed → obligation agent invoked → SES to uploader

Cancel Routing (POST /contracts/{id}/cancel-routing)
  └─ app.contract_signers rows deleted
  └─ status: ready_for_review
```

---

## 2. Component inventory

### 2.1 Lambda functions

All functions: Python 3.13, no VPC (database access via RDS Data API), log group
`/aws/lambda/<function-name>` with 30-day retention, one IAM role each.

| Function | Trigger | Memory | Timeout | Purpose |
|---|---|---|---|---|
| `rag-app-prod-signature-workflow` | HTTP API routes (section 2.2) | 128 MB | 30 s | Routes contracts, records signatures, sends SES emails, triggers obligation agent on completion |
| `rag-app-prod-admin-api` | HTTP API routes (section 2.2) | 256 MB | 29 s | Manages approval templates and system settings |
| `rag-app-prod-data-upload` | HTTP API `POST /upload` | 128 MB | 30 s | Generates presigned S3 URL; creates `app.contracts` row |
| `rag-app-prod-data-processor` | SQS + HTTP API | 128 MB | 60 s | Processes upload events; list/delete file API |

### 2.2 API routes

All routes require a valid Cognito `id_token` in the `Authorization` header. Unauthenticated
requests return `401` from the API Gateway authorizer.

| Method | Path | Function | Purpose |
|---|---|---|---|
| `POST` | `/upload` | data-upload | Generate presigned S3 URL; create contract record |
| `GET` | `/files` | data-processor | List all contracts for the authenticated user |
| `GET` | `/files/{documentId}/download` | data-processor | Get presigned download URL for original document |
| `DELETE` | `/files/{documentId}` | data-processor | Delete document from S3 and knowledge base |
| `POST` | `/contracts/{contractId}/route` | signature-workflow | Assign signers and send for signature |
| `GET` | `/contracts/{contractId}/signers` | signature-workflow | List signers and routing type for a contract |
| `POST` | `/contracts/{contractId}/sign` | signature-workflow | Record a signature (enforces turn order for sequential) |
| `POST` | `/contracts/{contractId}/cancel-routing` | signature-workflow | Cancel routing and reset to ready_for_review |
| `GET` | `/admin/templates` | admin-api | List approval templates |
| `PUT` | `/admin/templates/{templateId}` | admin-api | Update an approval template |
| `GET` | `/admin/settings` | admin-api | Get system settings |
| `PUT` | `/admin/settings` | admin-api | Save system settings |

### 2.3 Database tables (app schema)

All tables are in the `app` schema of `ragdb` (Aurora PostgreSQL). Managed by the `app_writer`
role; the AI role (`judy_ai_writer`) has read-only access to this schema.

| Table | Contents |
|---|---|
| `app.contracts` | One row per uploaded contract. Fields: `id` (UUID PK), `s3_bucket`, `s3_key`, `original_filename`, `mime_type`, `file_size_bytes`, `template_type`, `uploaded_by`, `uploaded_at`, `status`, `signed_at` |
| `app.signature_events` | Lifecycle audit log: `sent`, `signer_confirmed`, `completed`. One row per event. |
| `app.approval_templates` | Up to 3 admin-configured templates. Fields: `id`, `name`, `routing_type` (sequential\|parallel), `signer_roles` (JSONB ordered list), `is_default` (unique partial index). Default: sequential, roles `["supplier","company"]` |
| `app.contract_signers` | One row per signer assigned to a contract. Fields: `id`, `contract_id`, `template_id`, `signer_email`, `signer_role`, `signer_order`, `status` (pending\|signed\|declined), `assigned_at`, `signed_at` |
| `app.signer_signatures` | Actual signature data. Fields: `id`, `contract_id`, `signer_id`, `signature_data` (base64 PNG), `field_id` (optional link to AI-detected field), `page`, `created_at` |
| `app.system_settings` | Key/value table. Keys: `app_name`, `max_file_size_mb`, `notify_document_assigned`, `notify_signature_completed`, `notify_risk_flagged` |

### 2.4 Supporting resources

| Resource | Name / location | Notes |
|---|---|---|
| S3 landing zone | `rag-app-prod-landing-zone-580118073904-us-west-2-an` | Uploaded documents stored under `uploads/` prefix |
| SES | `us-east-1`, from address configured in `SES_FROM_ADDRESS` env var | Three email types: document assigned, signature completed, risk flagged |
| Frontend S3 bucket | `rag-app-prod-website-580118073904-us-west-2-an` | Static SPA files |
| CloudFront distribution | `E3H644UPA2I1GB`, domain `d8xv5mej9ouxr.cloudfront.net` | CDN for the frontend |
| DynamoDB | `rag-app-prod-documents` | Document metadata (kb_status, ingestion_job_id, uploader, size) |

---

## 3. Configuration

Environment variables set by Terraform (`IaC/6_lambda/main.tf`).

### signature-workflow

| Variable | Value | Meaning |
|---|---|---|
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | from SSM; `ragdb` | RDS Data API target |
| `SES_FROM_ADDRESS` | configured SES address | Sender address for all notification emails |
| `APP_URL` | CloudFront URL | Used in email links back to the application |
| `OBLIGATION_AGENT_FUNCTION_NAME` | `rag-app-prod-agent-obligation-tracking` | Invoked async when the last signer signs |

### admin-api

| Variable | Value | Meaning |
|---|---|---|
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | from SSM; `ragdb` | RDS Data API target |

### data-upload

| Variable | Value | Meaning |
|---|---|---|
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | from SSM; `ragdb` | RDS Data API target |
| `DYNAMODB_TABLE` | `rag-app-prod-documents` | Where initial document metadata is written |
| `S3_BUCKET` | landing zone bucket name | Target bucket for presigned upload URLs |

---

## 4. Signing workflow operations

### 4.1 Contract statuses

| Status | Meaning | Next action |
|---|---|---|
| `uploaded` | File received, AI analysis not yet started | Wait for analysis |
| `analyzing` | AI agents running | Wait |
| `ready_for_review` | Analysis complete | Review and send for signature |
| `routed_for_signature` | Signers assigned, waiting for signatures | Signers sign in order (or in parallel) |
| `signed` | All signers have signed | Obligations available |

### 4.2 Approval templates

Templates define who signs and in what order. Up to 3 templates; one is marked default.

| Field | Options | Effect |
|---|---|---|
| `routing_type` | `sequential` | Signers sign one at a time in `signer_order`; `/sign` rejects out-of-turn attempts with `403` |
| `routing_type` | `parallel` | All assigned signers can sign simultaneously; no order enforcement |
| `signer_roles` | e.g. `["supplier", "company"]` | Roles presented in the route modal; order determines `signer_order` for sequential contracts |
| `is_default` | true/false | The default template is pre-selected in the route modal |

To change templates: Admin panel → Templates → edit name, routing type or roles → Save.
Changes take effect immediately for new routings; contracts already routed are not affected.

### 4.3 Sending a contract for signature

1. In Contracts, click **Send for Signature** on a `ready_for_review` contract.
2. Enter signer email addresses and select roles (one row per signer, up to 5).
3. Click **Send for Signature** — the backend creates `app.contract_signers` rows and sends
   a notification email to all assigned signers simultaneously (sequential and parallel alike).
   There is no follow-up "your turn" email when the previous signer completes.

To re-send to different signers: click **Cancel Routing** first, then re-route. Cancel routing
deletes all existing signer rows and resets the contract to `ready_for_review`.

### 4.4 The signature

When a signer clicks **Sign**, a modal captures their signature as a base64-encoded PNG
(drawn on canvas or typed in a cursive font). The PNG is stored in `app.signer_signatures`.

For **sequential** contracts, the backend checks that no signer with a lower `signer_order` is
still pending before accepting the signature. Out-of-turn attempts return `403`. The UI also
hides the Sign button for non-turn signers, but the backend check is the authoritative guard.

For **parallel** contracts, any pending signer can sign in any order.

### 4.5 Completion

When the last pending signer signs:
- `app.contracts.status` is set to `signed`
- A `completed` event is inserted in `app.signature_events`
- SES email sent to the uploader
- `agent_obligation_tracking` is invoked asynchronously — failure is logged but does not
  affect the sign response

### 4.6 Email notifications

Three SES emails are sent by the signature workflow. The admin panel has toggles for each
(`notify_document_assigned`, `notify_signature_completed`, `notify_risk_flagged`) but the
current code does not read these settings — emails always go out. The toggles are a planned
control, not yet wired to the Lambda.

| Email | When sent | To |
|---|---|---|
| Document assigned | Contract routed for signature | All assigned signers (sequential and parallel alike) |
| Signature completed | All signers have signed | Uploader |
| Risk flagged | AI finds a high-severity risk on upload | Uploader |

SES is configured in `us-east-1`. The from address must be verified in SES before emails will
send. If emails are not arriving, check the SES sending quota and verify the from address in
the SES console (`us-east-1`).

---

## 5. Admin panel operations

The admin panel is accessible from the **Admin** link in the left sidebar. It is visible to all
authenticated users (no role separation in this version).

### 5.1 Approval templates

| Action | How |
|---|---|
| View templates | Admin → Templates tab |
| Edit name | Click the name field, type, click Save |
| Change routing type | Select Sequential or Parallel from the dropdown, click Save |
| Change signer roles | Edit the comma-separated roles field, click Save. Order matters for sequential. |
| Set default | Click **Set as Default** on the template to make it pre-selected in the route modal |

There is no way to add or delete templates from the UI in this version. The three templates
were seeded at database migration time (`IaC/2_data/scripts/003_signature_workflow.sql`). To
add a template, insert a row directly via the RDS Query Editor with `app_writer` credentials.

### 5.2 System settings

| Setting | Default | Effect |
|---|---|---|
| Application Name | `Judy.ai` | Display name in the UI header |
| Max File Size (MB) | `10` | Frontend validation limit for uploads |
| Email: Document assigned | enabled | Sends notification email when contract is routed |
| Email: Signature completed | enabled | Sends notification email when all parties sign |
| Email: Risk flagged | enabled | Sends notification email when AI flags a high-severity risk |

Click **Save Settings** to persist changes. Settings are stored in `app.system_settings` and
read on each relevant Lambda invocation.

---

## 6. Frontend

### 6.1 Overview

The frontend is a static single-page application (HTML/CSS/JavaScript, no build framework).
It is hosted in S3 and served through CloudFront.

| File | Purpose |
|---|---|
| `website/index.html` | Main SPA: Upload, Chat, Files, Contracts, Dashboard, Admin sections |
| `website/app.js` | All main page logic |
| `website/review.html` | Contract review and analysis page |
| `website/review.js` | Review page logic |
| `website/auth.js` | Cognito token handling |
| `website/config.js` | Auto-generated by Terraform — API URL, Cognito pool and client IDs |

> **Never edit `config.js` manually.** It is regenerated on every `terraform apply` in
> `IaC/4_cloudfront/` and any manual change will be overwritten on the next deploy.

### 6.2 Deploying a frontend change

```bash
export AWS_PROFILE=terraform-judy

# 1. Sync files to S3
aws s3 sync website/ s3://rag-app-prod-website-580118073904-us-west-2-an/ --delete

# 2. Invalidate CloudFront cache (takes ~1 minute to propagate)
aws cloudfront create-invalidation \
  --distribution-id E3H644UPA2I1GB \
  --paths "/*"
```

Hard-refresh the browser (`Cmd+Shift+R` / `Ctrl+Shift+R`) after the invalidation completes.

### 6.3 Authentication

The app uses Cognito OAuth2 implicit flow. `auth.js` checks `sessionStorage` for an
`id_token` on every page load. If missing or expired, the user is redirected to the Cognito
hosted UI. After login, `callback.html` extracts the token from the URL fragment and stores it.

Sessions last **1 hour**. After expiry the user is redirected to log in again. All chat
sessions stored in `sessionStorage` are cleared on logout.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Sign button not visible for a signer | Not their turn (sequential), or `/signers` fetch failed | Check browser console for fetch errors; verify the signer's email matches exactly (case-insensitive) |
| `403` on POST /sign | Signer is trying to sign out of turn on a sequential contract | Earlier signers must sign first; check `/signers` for pending signers with lower order |
| `409` on POST /sign | Signer has already signed | No action needed — already recorded |
| `404` on POST /sign | Signer email not in `app.contract_signers` | Re-route the contract with the correct email addresses |
| No notification email received | SES not verified, or `notify_*` setting is `false` | Check SES sending quota in `us-east-1`; check `app.system_settings` values |
| Contract stuck in `routed_for_signature` | A signer is not responding | Use Cancel Routing to reset, then re-route to different signers |
| Obligations missing after signing | Obligation agent failed or contract was signed before the agent existed | Re-run: `POST /contracts/{id}/analysis` with `{"agents": ["obligationTracking"]}` |
| Admin panel shows no templates | Database seed not applied | Check `IaC/2_data/scripts/003_signature_workflow.sql` was applied; re-run `terraform apply` in `IaC/2_data/` |
| Frontend shows old version after deploy | CloudFront cache not yet invalidated | Wait ~1 minute after invalidation, then hard-refresh |
| `config.js` missing or wrong API URL | Not regenerated after Terraform apply | Re-run `terraform apply` in `IaC/4_cloudfront/` |

---

## 8. Security controls

| Control | Evidence |
|---|---|
| Sign-in required on all routes | Unauthenticated requests → `401` from API Gateway Cognito authorizer |
| Sequential order enforced server-side | `/sign` returns `403` if an earlier signer is still pending, regardless of what the UI shows |
| Signature data stored, not re-transmitted | Base64 PNG stored in Aurora; never returned to the client after submission |
| S3 documents not publicly accessible | Public access block enabled on landing zone bucket; documents served via presigned URLs only |
| Presigned URLs scoped to regional endpoint | `data_processor` uses `endpoint_url=https://s3.us-west-2.amazonaws.com` to prevent redirect issues with browser PDF viewer |
| Least-privilege DB role | `app_writer` writes `app.*`, reads `judy_ai.*`; cannot modify AI results |
| SES from-address verified | Only verified addresses can send; prevents spoofing |
