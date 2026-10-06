# Judy AI — Handover: Backend & Frontend (15 minutes)

| | |
|---|---|
| Purpose | The backend and frontend section of the 1-hour handoff session (SOW acceptance criterion 13) |
| Audience | Developers and administrators taking over the Judy AI environment |
| Presenter | Backend / Frontend engineer |
| Reference | [`TECHNICAL_DOCUMENTATION.md`](TECHNICAL_DOCUMENTATION.md) for full architecture detail |

## Agenda

| Minutes | Part | Shown on screen |
|---|---|---|
| 0–2 | Project layout — what lives where | Repository structure |
| 2–7 | **Backend**: Lambdas, API, database | AWS Console + code |
| 7–12 | **Frontend**: SPA, auth, contracts UI | Live Judy interface |
| 12–15 | Deploying a change end-to-end | Terminal |

---

## 1. Project layout (2 min)

Everything lives in one repository: `Backend-JudyAI-aws`.

```
Backend-JudyAI-aws/
├── Backend/
│   ├── lambdas/        ← all Lambda function source (one folder per function)
│   ├── playbook/       ← AI risk playbook rules
│   ├── sql/            ← versioned DB migration files
│   └── tests/          ← offline and integration tests
├── IaC/                ← Terraform, numbered modules 1–8
│   ├── 1_network/
│   ├── 2_data/         ← Aurora, S3, DynamoDB, SQS, SNS, Bedrock KB
│   ├── 3_ses/
│   ├── 4_cloudfront/
│   ├── 5_cognito/
│   ├── 6_lambda/       ← all 12 Lambda deployments + IAM
│   ├── 7_apigateway/
│   └── 8_monitoring/
├── website/            ← static SPA (HTML/CSS/JS), deployed to S3 + CloudFront
└── Docs/               ← all documentation
```

Key rule: **infrastructure lives in `IaC/`, application code lives in `Backend/lambdas/` and
`website/`.** Never edit zips or generated files directly.

---

## 2. Backend (5 min)

### 2.1 Lambda functions

There are **12 Lambda functions**, all Python 3.13. Each has its own folder under
`Backend/lambdas/<function>/handler.py`.

| Function | What it does |
|---|---|
| `data_upload` | Generates presigned S3 URL; writes initial DynamoDB + Aurora `app.contracts` row |
| `data_processor` | Processes SQS upload events; handles list/delete file API; starts KB ingestion jobs |
| `ingestion_post_processor` | Monitors KB ingestion jobs via EventBridge Scheduler; updates DynamoDB status |
| `retrieve_and_generate` | Powers the AI chat — 3-node Strands Agents pipeline (safety → retrieval → generation) |
| `document_extraction` | Runs BDA on uploaded PDFs; stores extracted text; triggers the 3 pre-signing AI agents |
| `agent_risk_clause` | AI agent — risk and clause analysis |
| `agent_template_prepopulation` | AI agent — document type detection and field placement |
| `agent_summary` | AI agent — plain-language summary |
| `agent_obligation_tracking` | AI agent — obligation extraction (runs after last signature) |
| `signature_workflow` | Routes contracts for signature; records sign events; sends SES emails; triggers obligation agent on completion |
| `analysis_api` | Serves AI analysis results to the frontend; handles re-run requests |
| `admin_api` | Admin panel API — approval templates and system settings |

### 2.2 API

All routes go through **API Gateway HTTP API** (`rag-app-prod-api`) with a Cognito JWT
authorizer. Every request must carry a valid `id_token` in the `Authorization` header.

Key routes used by the frontend:

| Method | Path | Lambda |
|---|---|---|
| `POST` | `/upload` | data_upload |
| `GET` | `/files` | data_processor |
| `DELETE` | `/files/{documentId}` | data_processor |
| `POST` | `/contracts/{id}/route` | signature_workflow |
| `GET` | `/contracts/{id}/signers` | signature_workflow |
| `POST` | `/contracts/{id}/sign` | signature_workflow |
| `POST` | `/contracts/{id}/cancel-routing` | signature_workflow |
| `GET` | `/contracts/{id}/analysis` | analysis_api |
| `GET` | `/contracts/{id}/analysis/{agentType}` | analysis_api |
| `GET/PUT` | `/admin/templates` | admin_api |
| `GET/PUT` | `/admin/settings` | admin_api |

Full route table is in [`TECHNICAL_DOCUMENTATION.md`](TECHNICAL_DOCUMENTATION.md#api-layer).

### 2.3 Database

Aurora PostgreSQL 17.5 (`ragdb`), accessed by all Lambdas via the **RDS Data API** — no VPC
needed. Three schemas:

| Schema | Owner | Contents |
|---|---|---|
| `app` | Application team | Contracts, signers, signatures, approval templates, system settings |
| `judy_ai` | AI team | Extraction results, agent runs, risks, fields, summaries, obligations |
| `bedrock_integration` | Bedrock | pgvector embeddings for the knowledge base |

Each team has its own DB role with write access to its own schema and read-only access to the
other. The AI cannot write `app` records; the application cannot corrupt `judy_ai` data.

**DB migrations** are versioned SQL files in `Backend/sql/` (copied to `IaC/2_data/scripts/`).
They run automatically on `terraform apply` in `2_data` — no manual SQL needed.

### 2.4 Event-driven flow (upload → analysis)

```
User uploads file
  └─ Frontend PUTs to S3 (presigned URL)
       └─ S3 ObjectCreated → SNS → fan-out to 2 SQS queues
            ├─ ingestion queue → data_processor → Bedrock KB ingestion (for chat)
            └─ document-extraction queue → document_extraction
                 └─ BDA extracts text → stores in Aurora
                      └─ invokes in parallel:
                           ├─ agent_risk_clause
                           ├─ agent_template_prepopulation
                           └─ agent_summary
```

After the last signer signs:
```
signature_workflow → invokes agent_obligation_tracking
```

### 2.5 Deploying a Lambda change

1. Edit `Backend/lambdas/<function>/handler.py`
2. Run:
   ```bash
   export AWS_PROFILE=terraform-judy
   cd IaC/6_lambda
   terraform apply
   ```
   Terraform hashes the source folder and redeploys only changed functions.

3. If you changed the DB schema, add a migration file to `Backend/sql/`, copy it to
   `IaC/2_data/scripts/`, then `terraform apply` in `IaC/2_data/`.

---

## 3. Frontend (5 min)

### 3.1 What it is

A **static single-page application** — plain HTML, CSS, and JavaScript, no build step, no
framework. It is hosted in S3 and served globally through CloudFront.

```
website/
├── index.html      ← main SPA (upload, chat, files, contracts, dashboard, admin)
├── review.html     ← contract review / analysis page
├── callback.html   ← Cognito OAuth2 callback
├── app.js          ← all main page logic (~2,000 lines)
├── review.js       ← review page logic
├── auth.js         ← Cognito token handling
├── styles.css      ← dark theme
├── review.css
└── config.js       ← auto-generated by Terraform (API URL, Cognito config)
```

> **Never edit `config.js` by hand.** It is regenerated on every `terraform apply` in
> `IaC/4_cloudfront/`.

### 3.2 Authentication

The app uses **Cognito implicit flow** (OAuth2). On load, `auth.js` checks `sessionStorage` for
an `id_token`. If missing or expired, it redirects to the Cognito hosted UI. After login,
Cognito redirects to `callback.html`, which extracts the token from the URL fragment and stores
it in `sessionStorage`.

The `id_token` is a JWT. The app decodes its payload to get the signed-in user's email
(used to check whose turn it is to sign):

```js
function getCurrentUserEmail() {
    const token = sessionStorage.getItem("id_token");
    const payload = token.split(".")[1];
    const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return (decoded.email || "").toLowerCase();
}
```

Sessions last **1 hour**. After expiry the user is redirected to log in again.

### 3.3 Contracts and signing workflow

The contracts UI (`app.js`, bottom IIFE module) handles the full signature workflow:

| Action | What happens |
|---|---|
| **Send for Signature** | Opens route modal → user enters signer emails and roles → `POST /contracts/{id}/route` |
| **Sign** | Opens signature modal (draw or type) → captures canvas as base64 PNG → `POST /contracts/{id}/sign` |
| **Batch Sign** | User checks multiple contracts → one signature captured → applied to each in sequence |
| **View Signers** | Fetches `GET /contracts/{id}/signers` → shows each signer's status and order |
| **Cancel Routing** | `POST /contracts/{id}/cancel-routing` → resets contract to ready-for-review |
| **View Analysis** | Opens 4-tab modal (Risks / Fields / Summary / Obligations) |

**Sign button visibility**: on page load, for every contract in `routed_for_signature` status,
the frontend fetches `/signers` and hides the Sign button and batch checkbox if it is not the
current user's turn (lowest-order pending signer whose status is still `pending`).

### 3.4 Deploying a frontend change

1. Edit files under `website/`
2. Sync to S3:
   ```bash
   BUCKET="rag-app-prod-website-<account>-us-west-2-an"
   aws s3 sync website/ s3://$BUCKET/ --delete
   ```
3. Invalidate the CloudFront cache:
   ```bash
   DIST_ID="<cloudfront distribution id>"
   aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
   ```
4. Hard-refresh the browser (`Cmd+Shift+R`) to verify.

---

## 4. Deploying a change end-to-end (3 min)

### Backend change (e.g. fix in signature_workflow)

```bash
# 1. Edit the handler
vim Backend/lambdas/signature_workflow/handler.py

# 2. Deploy
export AWS_PROFILE=terraform-judy
cd IaC/6_lambda
terraform apply

# 3. Smoke test — sign a test contract through the UI and confirm the behaviour
```

### Frontend change (e.g. update a button or fix a UI bug)

```bash
# 1. Edit the file
vim website/app.js

# 2. Sync to S3
aws s3 sync website/ s3://$BUCKET/ --delete

# 3. Bust the cache
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"

# 4. Hard-refresh the browser and verify
```

### Adding a new API route

1. Add the handler logic in `Backend/lambdas/<function>/handler.py`
2. Register the route in `IaC/7_apigateway/config/prod.yaml`
3. Apply `6_lambda` then `7_apigateway`
4. Call the new endpoint from `app.js` or `review.js`

---

## Before the session

- [ ] Browser open on the live Judy interface and the AWS Console (Lambda + CloudWatch)
- [ ] A contract already uploaded and analysed, and one already fully signed
- [ ] Terminal ready with `AWS_PROFILE=terraform-judy` exported
- [ ] Know the CloudFront distribution ID and the website S3 bucket name
