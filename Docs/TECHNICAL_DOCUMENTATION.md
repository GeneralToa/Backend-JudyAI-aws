# Judy.ai Knowledge Base - Technical Documentation

## Table of Contents

1. [Solution Architecture](#solution-architecture)
2. [Component Descriptions](#component-descriptions)
3. [Agentic RAG Pipeline](#agentic-rag-pipeline)
4. [Data Flows](#data-flows)
5. [Configuration](#configuration)
6. [Architectural Decisions](#architectural-decisions)
7. [Terraform & Source Code Structure](#terraform--source-code-structure)
8. [DevOps: Deployment & Maintenance](#devops-deployment--maintenance)
9. [Post-Deployment: Knowledge Base Setup with Default Data](#post-deployment-knowledge-base-setup-with-default-data)

---

## Solution Architecture

```
                                    ┌──────────────────────────────┐
                                    │         End Users             │
                                    └──────────────┬───────────────┘
                                                   │
                                    ┌──────────────▼───────────────┐
                                    │     CloudFront (CDN + OAC)    │
                                    └──────────────┬───────────────┘
                                                   │
                              ┌─────────────────────┼─────────────────────┐
                              │                     │                     │
                   ┌──────────▼──────────┐  ┌──────▼──────┐   ┌─────────▼─────────┐
                   │  S3 (Static Website) │  │   Cognito   │   │   API Gateway     │
                   │  index.html, app.js  │  │  User Pool  │   │  (REST + Auth)    │
                   └──────────────────────┘  └─────────────┘   └───────┬───────────┘
                                                                       │
                          ┌────────────────────────────────────────────┼────────────────┐
                          │                                            │                │
               ┌──────────▼──────────┐              ┌─────────────────▼──────┐   ┌─────▼──────────────┐
               │  Lambda: data_upload │              │  Lambda: data_processor │   │  Lambda: retrieve  │
               │  (presigned URLs +   │              │  (files API + ingestion)│   │  _and_generate     │
               │   DynamoDB write)    │              └────────────┬───────────┘   │  (Strands Agents)  │
               └──────────┬───────────┘                          │                └─────────┬──────────┘
                          │                                      │                          │
                          ▼                                      ▼                          ▼
               ┌────────────────────┐              ┌─────────────────────┐     ┌───────────────────────┐
               │  S3 Landing Zone   │──S3 Event──▶│       SQS Queue     │     │  Bedrock Knowledge    │
               │  (document storage)│              └──────────┬──────────┘     │  Base (retrieve &     │
               └────────────────────┘                         │                │  generate API)        │
                                                              ▼                └───────────┬───────────┘
                                                   ┌────────────────────┐                  │
                                                   │  Bedrock KB Sync   │                  ▼
                                                   │  (StartIngestion)  │     ┌───────────────────────┐
                                                   └─────────┬──────────┘     │  Aurora Serverless v2  │
                                                             │                │  (PostgreSQL+pgvector) │
                                                             ▼                └───────────────────────┘
                                                   ┌────────────────────┐
                                                   │  EventBridge       │
                                                   │  Scheduler         │
                                                   │  (dynamic, 1 min)  │
                                                   └─────────┬──────────┘
                                                             ▼
                                                   ┌────────────────────┐
                                                   │  Lambda: ingestion │
                                                   │  _post_processor   │
                                                   └────────────────────┘
                                                             │
                                                             ▼
                                                   ┌────────────────────┐
                                                   │     DynamoDB       │
                                                   │  (document metadata│
                                                   │   + kb_status)     │
                                                   └────────────────────┘
```

---

## Component Descriptions

### Frontend

| Component | Technology | Purpose |
|-----------|------------|---------|
| Static Website | HTML/CSS/JS | Judy.ai branded SPA with upload, chat, files pages |
| Authentication | Cognito Hosted UI | OAuth2 implicit flow with custom dark theme CSS |
| Hosting | CloudFront + S3 | CDN delivery with Origin Access Control |

### API Layer

| Component | Technology | Purpose |
|-----------|------------|---------|
| API Gateway | REST API (Regional) | Routes with Cognito JWT authorizer |
| Routes | POST /upload, POST /chat, GET /files, DELETE /files/{id} | CRUD operations + RAG queries |
| CORS | OPTIONS preflight | Allows cross-origin requests from CloudFront domain |

### Compute (Lambda Functions)

| Function | Runtime | Timeout | Memory | Purpose |
|----------|---------|---------|--------|---------|
| data_upload | Python 3.13 | 30s | 128MB | Generates presigned S3 URLs, writes initial DynamoDB metadata |
| data_processor | Python 3.13 | 60s | 128MB | Handles SQS events (ingestion), API GW (list/delete files) |
| retrieve_and_generate | Python 3.13 | 120s | 512MB | Agentic RAG pipeline (Strands Agents + Bedrock KB) |
| ingestion_post_processor | Python 3.13 | 30s | 128MB | Monitors ingestion jobs, updates DynamoDB status |

### Data Storage

| Component | Technology | Purpose |
|-----------|------------|---------|
| S3 Landing Zone | Standard bucket | Raw document storage (PDF/DOCX uploads) |
| DynamoDB | PAY_PER_REQUEST | Document metadata (id, name, status, uploader, size) |
| Aurora Serverless v2 | PostgreSQL 16.6 + pgvector | Vector store for Bedrock KB embeddings |
| Secrets Manager | JSON secret | Bedrock KB database credentials |
| SSM Parameter Store | Standard parameters | Knowledge Base ID and Data Source ID |

### AI/ML

| Component | Model | Purpose |
|-----------|-------|---------|
| Bedrock Knowledge Base | - | Orchestrates ingestion (OCR, chunking, embedding) and retrieval |
| Embedding Model | Cohere Embed Multilingual v3 | Generates 1024-dim vectors for documents |
| Foundation Model | Amazon Nova Pro v1 | RAG generation and response formatting |
| Intent Detection | Amazon Nova Lite v1 | Content safety classification |
| Retrieval Agent | Amazon Nova Lite v1 | KB search with rephrase retry logic |
| BDA Parser | Bedrock Data Automation | Multi-modal document parsing (PDF/images) |

### Event-Driven Components

| Component | Purpose |
|-----------|---------|
| SQS Standard Queue | Buffers S3 upload events for serial ingestion processing |
| SQS Dead Letter Queue | Captures failed messages after 10 retries |
| EventBridge Scheduler | Dynamic schedules (1-min rate) to monitor ingestion job status |
| CloudWatch Alarm | Alerts when DLQ has messages (processing failures) |

---

## Agentic RAG Pipeline

The `retrieve_and_generate` Lambda implements a 3-node agent graph using the Strands Agents SDK:

```
User Query
    │
    ▼
┌─────────────────────────────────┐
│  Intent Detector (Nova Lite)    │
│  - Classifies SAFE/UNSAFE       │
│  - Blocks harmful queries        │
└──────────────┬──────────────────┘
               │ (only if SAFE)
               ▼
┌─────────────────────────────────┐
│  Retrieval Agent (Nova Lite)    │
│  - Calls retrieve_from_kb tool   │
│  - If no result: rephrases       │
│  - If still no result: uses      │
│    general LLM knowledge          │
│  - Checks citations for relevance│
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Response Generator (Nova Pro)  │
│  - Formats tables, lists, bold   │
│  - Source attribution rules       │
│  - Never fabricates information   │
└──────────────┬──────────────────┘
               │
               ▼
         Final Response
```

### Key Design Choices

- **Fail-open safety**: If intent detector output is ambiguous, query proceeds (only explicit "UNSAFE:" blocks)
- **Citations-based detection**: Uses Bedrock `citations[]` field (not heuristic text matching) to determine if KB had relevant results
- **Module-level initialization**: Graph is built once at Lambda cold start, reused across warm invocations
- **Adaptive retry**: boto3 clients configured with `mode: adaptive` for automatic throttle handling

---

## Data Flows

### Upload Flow

```
1. User drops file → Frontend validates size (15MB max)
2. POST /upload → data_upload Lambda
3. Lambda generates presigned URL + writes DynamoDB record (kb_status="loading")
4. Frontend PUTs file to S3 via presigned URL
5. S3 ObjectCreated event → SQS → data_processor Lambda
6. Lambda updates file_size_kb in DynamoDB
7. Lambda calls StartIngestionJob (Bedrock KB sync)
8. Lambda creates EventBridge Scheduler schedule (rate: 1 min)
9. Scheduler triggers ingestion_post_processor
10. Post-processor checks job status via GetIngestionJob
11. On COMPLETE: updates kb_status to "ingested", deletes schedule
12. On FAILED: updates kb_status to "failed", deletes schedule
```

### Deletion Flow

```
1. User right-clicks file → confirms deletion
2. DELETE /files/{id} → data_processor Lambda
3. Lambda deletes file from S3
4. Lambda sets kb_status="deleting" in DynamoDB (record kept)
5. Lambda calls StartIngestionJob (re-sync)
6. Lambda creates EventBridge Scheduler schedule
7. Post-processor monitors job → on COMPLETE: deletes DynamoDB record + schedule
```

### Conflict Handling (Orphan Pattern)

```
When StartIngestionJob throws ConflictException (job already running):
- Document left as "orphan" (no ingestion_job_id)
- When the running job's post-processor completes:
  1. Processes its own tagged documents
  2. Checks for orphans (loading/deleting without job_id)
  3. If found: starts follow-up ingestion → tags orphans → creates new schedule
  4. Chain continues until all documents reach terminal state
```

---

## Configuration

### Terraform Variables

| Variable | Default | Description |
|----------|---------|-------------|
| aws_region | us-east-1 | AWS region for all resources |
| project_name | rag-app | Prefix for resource naming |
| environment | dev | Environment identifier |
| aurora_master_username | dbadmin | Aurora admin username |
| aurora_master_password | (required) | Aurora admin password |
| aurora_database_name | ragdb | Database name |
| aurora_bedrock_user_username | bedrock_user | Bedrock KB database role |
| aurora_bedrock_user_password | (required) | Must match SQL setup script |
| bedrock_foundation_model_id | amazon.nova-pro-v1:0 | Model for RAG generation |
| bedrock_embedding_model_id | cohere.embed-multilingual-v3 | Model for vectorization |

### Resource Tagging

All resources are tagged with:
```
aws-apn-id = "pc:an2wwsvdun8wdc006lw2pr8ag"
```

Applied globally via `provider.aws.default_tags`.

### Aurora Configuration

| Setting | Value |
|---------|-------|
| Engine | aurora-postgresql 16.6 |
| Instance Class | db.serverless |
| Min ACU | 1 |
| Max ACU | 1 |
| Storage Encrypted | true |
| HTTP Endpoint (Data API) | enabled |
| Publicly Accessible | true (dev only) |

---

## Architectural Decisions

### 1. S3 → SQS → Lambda (not S3 → Lambda direct)

**Decision**: Route S3 events through SQS before Lambda.

**Rationale**: S3 direct Lambda triggers don't support fan-out or retry with DLQ. SQS provides message durability, retry (10 attempts), and dead-letter queue for failed processing.

### 2. Dynamic EventBridge Scheduler (not static scheduled rule)

**Decision**: Lambda creates/deletes EventBridge Scheduler schedules dynamically per ingestion job.

**Rationale**: A static rule (running every N minutes perpetually) wastes compute when no jobs are active. Dynamic schedules exist only during active ingestion, then self-delete. Cost: $0 when idle.

### 3. DynamoDB for document metadata (not S3 metadata)

**Decision**: Store file metadata in DynamoDB, not S3 object metadata or tags.

**Rationale**: DynamoDB supports fast scans, attribute updates (kb_status lifecycle), and doesn't require listing S3 objects (which is eventually consistent and slow).

### 4. Strands Agents graph pattern (not monolithic prompt)

**Decision**: Three-agent pipeline with separate models per task.

**Rationale**: Separation of concerns (safety, retrieval, formatting), different model sizes for different tasks (Lite for classification, Pro for generation), and fail-isolation (one agent failing doesn't crash the whole pipeline).

### 5. Bedrock Data Automation (BDA) parsing strategy

**Decision**: Use BDA for document parsing instead of the default parser.

**Rationale**: BDA provides superior OCR, table extraction, and multi-modal understanding for PDFs with images, charts, and complex layouts.

### 6. Cognito Implicit flow (not Authorization Code)

**Decision**: Use OAuth2 implicit flow for the static site.

**Rationale**: Simplest implementation for a pure client-side SPA with no backend token exchange. The token is stored in sessionStorage (cleared on tab close/logout). Acceptable for the current security requirements.

### 7. Job-document association (ingestion_job_id)

**Decision**: Tag each DynamoDB document with the specific ingestion_job_id that processes it.

**Rationale**: Without this, a global scan for "loading" documents could incorrectly mark files processed by different concurrent jobs. The association ensures per-job precision.

---

## Terraform & Source Code Structure

### Directory Layout

```
.
├── docs/
│   ├── USER_MANUAL.md
│   └── TECHNICAL_DOCUMENTATION.md
├── terraform/
│   ├── main.tf                    # Root module - wires all sub-modules
│   ├── variables.tf               # Input variable declarations
│   ├── outputs.tf                 # Terraform outputs (URLs, IDs)
│   ├── providers.tf               # AWS provider + default tags
│   ├── terraform.tfvars           # Actual deployment values (gitignored)
│   ├── terraform.tfvars.example   # Template for new deployments
│   └── modules/
│       ├── s3/                    # Website bucket + Landing Zone bucket
│       │   ├── main.tf            # Buckets, CORS, website file uploads, EventBridge notification
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── cloudfront/            # Distribution + OAC + S3 bucket policy
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── cognito/               # User Pool + Client + UI customization
│       │   ├── main.tf
│       │   ├── cognito-custom.css # Dark theme for hosted login UI
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── api_gateway/           # REST API + 4 routes + Cognito authorizer
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── lambda/                # 4 Lambda functions + IAM + EventBridge Scheduler role
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── aurora/                # Serverless v2 cluster + Secrets Manager
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── bedrock_kb/            # Knowledge Base + S3 data source + BDA IAM
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── dynamodb/              # Documents metadata table
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── sqs/                   # Ingestion queue + DLQ + S3 notification + alarm
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       └── ssm/                   # KB ID + DS ID parameters
│           ├── main.tf
│           ├── variables.tf
│           └── outputs.tf
├── lambdas/
│   ├── data_upload/
│   │   └── handler.py            # Presigned URL + DynamoDB metadata write
│   ├── data_processor/
│   │   └── handler.py            # SQS handler + Files API + ingestion orchestration
│   ├── retrieve_and_generate/
│   │   └── handler.py            # Strands Agents graph (3-node agentic RAG)
│   └── ingestion_post_processor/
│       └── handler.py            # Job status polling + orphan follow-up
├── website/
│   ├── index.html                # Main SPA page (sidebar + 3 sections)
│   ├── callback.html             # Cognito OAuth callback (token capture)
│   ├── styles.css                # Full dark theme CSS (Judy.ai design system)
│   ├── app.js                    # Application logic (upload, chat, files, sessions)
│   ├── auth.js                   # Cognito auth (login redirect, logout, token management)
│   ├── logo-color.svg            # Judy.ai color logo (inline SVG in HTML)
│   └── config.js                 # Auto-generated by Terraform (API URLs, Cognito config)
└── sql/
    └── setup_vector_db.sql       # Aurora pgvector schema setup script
```

### Key Terraform Resources by Module

| Module | Resources Created |
|--------|-------------------|
| s3 | 2 buckets, CORS, public access block, website file objects, EventBridge notification |
| cloudfront | Distribution, OAC, S3 bucket policy |
| cognito | User Pool, Domain, App Client, UI Customization |
| api_gateway | REST API, Authorizer, 4 resources, 4 methods, 4 CORS OPTIONS, deployment, stage, 3 Lambda permissions |
| lambda | 4 IAM roles, 4 IAM policies, 4 archive files, 4 Lambda functions, 1 SQS event source mapping, 1 Lambda layer reference, 1 Scheduler execution role |
| aurora | RDS cluster, instance, Secrets Manager secret + version |
| bedrock_kb | IAM role + policy, Knowledge Base, S3 data source (BDA parsing) |
| dynamodb | Single table (PAY_PER_REQUEST, document_id PK) |
| sqs | Standard queue, DLQ, queue policy, S3 notification, CloudWatch alarm |
| ssm | 2 parameters (KB ID, DS ID) |

---

## DevOps: Deployment & Maintenance

### Prerequisites

- Terraform >= 1.5.0
- AWS CLI v2 configured with credentials
- Access to Amazon Bedrock models (enable in the Bedrock console):
  - Cohere Embed Multilingual v3
  - Amazon Nova Pro v1
  - Amazon Nova Lite v1
- PostgreSQL client (`psql`) for database setup

### Step 1: Configure Variables

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values (passwords, region, etc.)
```

### Step 2: Deploy Infrastructure

```bash
terraform init
terraform plan    # Review changes
terraform apply   # Deploy (type 'yes' to confirm)
```

Expected output includes:
- `cloudfront_distribution_url` - Application URL
- `cognito_user_pool_id` - For user management
- `knowledge_base_id` - KB identifier
- `aurora_cluster_endpoint` - Database endpoint

### Step 3: Setup Aurora Database

Connect to Aurora and run the SQL setup script:

```bash
# Get the cluster endpoint
ENDPOINT=$(terraform output -raw aurora_cluster_endpoint)

# Connect using psql
psql -h $ENDPOINT -U <aurora_master_username> -d ragdb

# Run the setup script (replace placeholders first!)
\i ../sql/setup_vector_db.sql
```

**Critical**: Replace `BEDROCK_USERNAME` and `BEDROCK_PASSWORD` in the SQL script with the exact values from your `terraform.tfvars` (`aurora_bedrock_user_username` and `aurora_bedrock_user_password`).

### Step 4: Create Initial User

```bash
USER_POOL_ID=$(terraform output -raw cognito_user_pool_id)

aws cognito-idp admin-create-user \
  --user-pool-id $USER_POOL_ID \
  --username admin@yourcompany.com \
  --user-attributes Name=email,Value=admin@yourcompany.com Name=email_verified,Value=true \
  --temporary-password TempPassword123!
```

### Step 5: Invalidate CloudFront Cache (after updates)

```bash
DIST_ID=$(terraform output -raw cloudfront_distribution_id)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

### Updating the Application

```bash
cd terraform
terraform apply  # Detects file changes via etag/hash, re-uploads
# Then invalidate CloudFront cache for immediate effect
```

### Destroying the Environment

```bash
cd terraform
terraform destroy  # Type 'yes' to confirm
```

**Warning**: This deletes ALL resources including the Aurora database and S3 data. Ensure you have backups of any important data.

---

## Post-Deployment: Knowledge Base Setup with Default Data

After infrastructure deployment, the Knowledge Base is empty. This section describes how to load default/seed data so users have content available immediately.

### Overview

The process:
1. Prepare default documents (PDF/DOCX)
2. Upload them to the S3 Landing Zone
3. Trigger a Knowledge Base synchronization job
4. Validate that data is queryable via the Retrieve & Generate API

### Step 1: Prepare Default Documents

Gather the PDF/DOCX files that should be pre-loaded. Place them in a local directory:

```bash
mkdir -p default-data/
# Copy your default documents here
cp /path/to/document1.pdf default-data/
cp /path/to/document2.pdf default-data/
```

### Step 2: Upload to S3 Landing Zone

Upload documents to the landing zone bucket using the same path pattern the application uses (`uploads/{uuid}/{filename}`):

```bash
# Get the bucket name
BUCKET=$(terraform output -raw landing_zone_bucket)

# Upload each file with a unique UUID prefix
for file in default-data/*; do
  UUID=$(uuidgen | tr '[:upper:]' '[:lower:]')
  FILENAME=$(basename "$file")
  aws s3 cp "$file" "s3://${BUCKET}/uploads/${UUID}/${FILENAME}"
  echo "Uploaded: uploads/${UUID}/${FILENAME}"
done
```

**Note**: Uploading via S3 directly (not the app UI) means DynamoDB records won't be created for these files. They will exist in the KB but not appear in the "Files" page. If you want them visible in the UI, upload via the application's upload page instead.

### Step 3: Trigger Knowledge Base Synchronization

```bash
# Get KB and DS IDs
KB_ID=$(terraform output -raw knowledge_base_id)
DS_ID=$(terraform output -raw data_source_id)

# Start ingestion job
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID \
  --region us-east-1
```

Note the `ingestionJobId` from the response.

### Step 4: Monitor Synchronization Progress

```bash
# Check job status (repeat until status is "COMPLETE")
aws bedrock-agent get-ingestion-job \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID \
  --ingestion-job-id <JOB_ID> \
  --region us-east-1
```

Expected output when complete:
```json
{
  "ingestionJob": {
    "status": "COMPLETE",
    "statistics": {
      "numberOfDocumentsScanned": 5,
      "numberOfNewDocumentsIndexed": 5,
      "numberOfDocumentsFailed": 0
    }
  }
}
```

**If `numberOfDocumentsFailed` > 0**: Check the `failureReasons` field for details. Common issues:
- File too large (max 50MB for Bedrock KB)
- Unsupported format
- IAM permission issues

### Step 5: Validate with Retrieve & Generate API

Test that the default data is queryable:

```bash
# Test a query against the knowledge base
aws bedrock-agent-runtime retrieve-and-generate \
  --input '{"text": "What are the main topics covered in the documents?"}' \
  --retrieve-and-generate-configuration '{
    "type": "KNOWLEDGE_BASE",
    "knowledgeBaseConfiguration": {
      "knowledgeBaseId": "'$KB_ID'",
      "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    }
  }' \
  --region us-east-1
```

Verify:
- The response contains relevant information from your uploaded documents
- Citations reference the uploaded S3 keys
- No errors in the response

### Step 6: Validate via the Application UI

1. Open the application URL in a browser
2. Log in with the admin user
3. Navigate to **Chat**
4. Ask a question about the default documents
5. Verify you receive a relevant, sourced answer

### Alternative: Upload Default Data via the Application UI

If you want the files to appear in the Files page with proper metadata:

1. Log into the application
2. Navigate to **Upload**
3. Upload each default document through the drag-and-drop interface
4. Navigate to **Files** and wait for status to change to "Ingested"
5. Test via Chat

This approach is simpler but requires manual effort per file.

### Troubleshooting Default Data Setup

| Issue | Cause | Resolution |
|-------|-------|------------|
| Ingestion job fails | IAM permissions | Check Bedrock KB role has S3 GetObject and ListBucket on the landing zone bucket |
| Job completes but 0 docs indexed | Files not in correct prefix | Ensure files are under `uploads/` prefix (or any prefix configured on the data source) |
| Query returns no results | Vector dimension mismatch | Verify Aurora table uses `vector(1024)` matching Cohere Embed v3 |
| Query returns no results | DB credentials mismatch | Verify Secrets Manager credentials match the PostgreSQL role created in SQL setup |
| "STARTING" stuck for minutes | Normal for BDA parsing | BDA takes longer than default parser; wait up to 5 minutes |
