# Judy AI — Administrator Reference: AI and Data Management

| | |
|---|---|
| Scope | The AI workstream of the SOW: document text extraction, the four AI agents, the AI data model and the AI API |
| Audience | Administrators who deploy, configure and operate the Judy AI environment |
| Environment | AWS account `580118073904`, region `us-west-2`, resource prefix `rag-app-prod` |
| Version | 1.3, 2 October 2026. Values read from the live environment on 1 and 2 October. 1.1: extraction batch handling and rules packaging fixed. 1.2: per-user access on the AI API. 1.3: all three live; DLQ retention 14 days. |
| Related | Infrastructure and application chapters of this reference; the API contract in [`AI_AGENTS_API.md`](AI_AGENTS_API.md) |

This chapter is part of the single administrator reference document the SOW requires
(acceptance criterion 12). It covers deployment, configuration and maintenance of the AI
components only. Networking, Cognito, API Gateway, the web application and the signing
workflow are covered in their own chapters.

---

## 1. What the AI does

When a contract is uploaded, its text and page layout are extracted with Amazon Bedrock Data
Automation (BDA), and three agents analyse it before anyone is asked to sign. When the last
signer confirms, a fourth agent extracts the obligations to track. Every result is stored in
PostgreSQL and served to the web application through the AI API.

```
upload to S3 (uploads/)
  └─ SNS rag-app-prod-upload-assets-topic
       └─ SQS rag-app-prod-document-extraction-sqs  ──(3 failed attempts)──>  ...-sqs-dlq
            └─ Lambda document-extraction  ── BDA project judy-ai-contract-extraction
                 ├─ writes judy_ai.document_extractions
                 └─ invokes, asynchronously and in parallel:
                      ├─ agent-risk-clause             → judy_ai.contract_risks
                      ├─ agent-template-prepopulation  → judy_ai.template_detections, contract_fields
                      └─ agent-summary                 → judy_ai.contract_summaries

last signature (signature-workflow Lambda)
  └─ invokes agent-obligation-tracking               → judy_ai.contract_obligations

web application ── HTTP API rag-app-prod-api (Cognito sign-in) ── Lambda analysis-api
                   status, results, re-run, page images             (reads Postgres, invokes agents)
```

| Agent | Runs | Produces |
|---|---|---|
| Risk & clause analysis | Before signing | Findings labelled unusual term, missing clause, date mismatch, compliance gap or tracked term; each with severity, the quoted sentence and page; advisory replacement wording taken word for word from Judy's playbook where a playbook rule applies |
| Template pre-population | Before signing | Document type (MSA, purchase agreement, SOW, or not one of the three) with the reason; every signature block and fill-in field with its signer role, page and position |
| Plain-language summary | Before signing | A 120–260 word overview and 5–10 key points, each tied to the sentence it came from |
| Obligation tracking | After the last signature | Commitments, milestones, renewal and termination windows; who owes each; the document's own timing words; a calendar date only when the document writes one |

Design rules every agent follows, and which an administrator should not change without the
client's agreement: the AI never writes contract language (suggested wording is looked up from
the playbook, never generated); every result points to its sentence and page; field positions
come from the page layout BDA measured, never from the model; dates are never calculated.

---

## 2. Component inventory

### 2.1 Lambda functions

All functions: Python 3.13, no VPC (database access is through the RDS Data API), log group
`/aws/lambda/<function name>` with 30-day retention, one IAM role each.

| Function | Trigger | Memory | Timeout | Role |
|---|---|---|---|---|
| `rag-app-prod-document-extraction` | SQS `rag-app-prod-document-extraction-sqs`, batch size 10, documents processed in parallel | 256 MB | 300 s | `rag-app-prod-documentExtraction-role` |
| `rag-app-prod-agent-risk-clause` | Async invoke by extraction or analysis-api | 512 MB | 300 s | `rag-app-prod-agentRiskClause-role` |
| `rag-app-prod-agent-template-prepopulation` | Async invoke by extraction or analysis-api | 512 MB | 300 s | `rag-app-prod-agentTemplatePrepopulation-role` |
| `rag-app-prod-agent-summary` | Async invoke by extraction or analysis-api | 512 MB | 300 s | `rag-app-prod-agentSummary-role` |
| `rag-app-prod-agent-obligation-tracking` | Async invoke by signature-workflow or analysis-api | 512 MB | 300 s | `rag-app-prod-agentObligationTracking-role` |
| `rag-app-prod-analysis-api` | HTTP API routes (section 2.4) | 256 MB | 29 s | `rag-app-prod-analysisApi-role` |

### 2.2 Supporting resources

| Resource | Name / location | Notes |
|---|---|---|
| Upload bucket | `rag-app-prod-landing-zone-580118073904-us-west-2-an` | Only the `uploads/` prefix triggers processing. BDA output is written under `bda-output/`. SSE (AES-256), public access blocked. |
| Upload topic | SNS `rag-app-prod-upload-assets-topic` | Raw message delivery is off: queue messages are SNS envelopes, which the extraction code unwraps. |
| Extraction queue | SQS `rag-app-prod-document-extraction-sqs` | Visibility 360 s, retention 14 days, `maxReceiveCount` 3, encrypted. |
| Dead-letter queue | SQS `rag-app-prod-document-extraction-sqs-dlq` | Documents that failed extraction three times. Retention 14 days (raised from 1 day on 1 Oct 2026). |
| BDA project | `judy-ai-contract-extraction` (`…:data-automation-project/3582b7d2b55a`), ARN in SSM `/rag-app-prod/bedrock/data-automation-project-arn` | Granularity PAGE + ELEMENT + LINE, bounding boxes enabled. Managed in Terraform. Do not switch to the public default project: it returns no bounding boxes, and field placement depends on them. |
| BDA profile | `us.data-automation-v1` | Cross-region profile; IAM evaluates it in other regions, so the policy allows it with a wildcard region. |
| Foundation model | Amazon Nova Pro through inference profile `us.amazon.nova-pro-v1:0` | Temperature 0 in every agent. No training or fine-tuning. |
| Database | Aurora PostgreSQL `rag-app-prod-aurora-postgres`, database `ragdb`, Data API enabled | Cluster ARN in SSM `/rag-app-prod/aurora/postgres-arn`. Storage encrypted. |
| AI database credentials | Secrets Manager `/rds/rag-app-prod-judy-ai-writer/credentials`, ARN in SSM `/rag-app-prod/secret-manager/judy-ai-writer-secret` | Role `judy_ai_writer`: writes the `judy_ai` schema, reads `app`, cannot write `app`. |

### 2.3 Where the code and configuration live

| What | Path in `Backend-JudyAI-aws` |
|---|---|
| Function source (this is what gets deployed) | `Backend/lambdas/<function>/handler.py` |
| Playbook converter and rules | `Backend/playbook/build_rules.py` → `Backend/playbook/playbook_rules.json` (the source `.docx` is Judy's document and is kept outside the repository) |
| Database migrations | `Backend/sql/001_ai_schema.sql`, `002_risk_category_tracked_term.sql` (applied copies in `IaC/2_data/scripts/`) |
| Terraform for the functions | `IaC/6_lambda/` — `config/prod.yaml` (memory, timeout, triggers), `main.tf` (environment variables), `iam.tf` (roles) |
| Build output | `IaC/6_lambda/functions/<function>/function.zip` (not committed), produced by Terraform's `archive_file` from `Backend/lambdas/<function>/` |
| Validation tools | `Backend/tests/` (section 6.7) |

### 2.4 AI API routes

All on the HTTP API `rag-app-prod-api` behind the Cognito JWT authorizer; a request without a
valid sign-in gets `401` from the gateway. A signed-in user sees a contract's analysis only if
they uploaded it or are one of its signers — the same rule as the contract list. Anyone else
gets `404`, as for an unknown contract. Full request and response shapes are in
[`AI_AGENTS_API.md`](AI_AGENTS_API.md).

| Route | Purpose |
|---|---|
| `GET /contracts/{contractId}/analysis` | Status of extraction and each agent; `?include=pageImages` adds presigned page images (Word documents only) |
| `GET /contracts/{contractId}/analysis/{agentType}` | Results of one agent: `risk-clause`, `template-prepopulation`, `summary`, `obligation-tracking` |
| `POST /contracts/{contractId}/analysis` | Re-run agents (body optional, see section 6.2) |

---

## 3. Configuration

Environment variables are set by Terraform (`IaC/6_lambda/main.tf`). Variables marked
*default* are not set in Terraform; the code uses the value shown. Change them on the function
or, better, add them to `main.tf` so the change survives the next deploy.

| Variable | Functions | Value | Meaning |
|---|---|---|---|
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | all six | from SSM; `ragdb` | Data API target and the least-privilege AI secret |
| `MODEL_ID` | the four agents | `us.amazon.nova-pro-v1:0` | Must be an **inference profile** ID. A bare model ID fails with "on-demand throughput isn't supported". |
| `PROMPT_VERSION` | the four agents | *default* `risk-clause-v1`, `template-prepopulation-v1`, `summary-v1`, `obligation-tracking-v1` | Stored on every run so results can be traced to the prompt that produced them. Bump it when a prompt changes. |
| `MAX_CONTRACT_CHARS` | the four agents | *default* 120000 | Longer documents are truncated before the model sees them (about 40 pages; the SOW limit is 20 pages). |
| `BDA_PROJECT_ARN`, `BDA_PROFILE_ARN` | document-extraction | from SSM; profile `us.data-automation-v1` | Which BDA project and profile to use |
| `BDA_OUTPUT_BUCKET`, `BDA_OUTPUT_PREFIX` | extraction (both), template agent (bucket) | landing-zone bucket; `bda-output` | Where BDA writes `result.json` and page images |
| `PRE_SIGNING_AGENT_ARNS` | document-extraction | the three pre-signing agents, comma-separated | Which agents run after extraction |
| `MAX_WAIT_SECONDS`, `POLL_INTERVAL_SECONDS` | document-extraction | *default* 240, 5 | How long extraction waits for BDA. A job still running after this is left as `running`. |
| `MAX_PARALLEL_DOCUMENTS` | document-extraction | *default* 10 | Uploads of one queue batch processed at the same time |
| `EXTRACTION_WAIT_SECONDS` | obligation agent | *default* 120 | If a contract is signed within a minute of upload, the agent waits this long for its extraction. |
| `RISK_AGENT_ARN`, `TEMPLATE_AGENT_ARN`, `SUMMARY_AGENT_ARN`, `OBLIGATION_AGENT_ARN` | analysis-api | the four agent ARNs | Targets for re-runs |
| `IN_FLIGHT_WINDOW_SECONDS` | analysis-api | *default* 600 | A run still pending or running after this long is treated as dead, so it cannot block re-runs. |
| `PAGE_IMAGE_URL_TTL` | analysis-api | *default* 900 | Lifetime of presigned page-image URLs in seconds. The web application refreshes them before expiry. |
| `OBLIGATION_AGENT_FUNCTION_NAME` | signature-workflow | `rag-app-prod-agent-obligation-tracking` | The completion hook's target (application chapter) |

---

## 4. Deployment

### 4.1 How code reaches AWS

Terraform's `archive_file` zips each folder `Backend/lambdas/<function>/` and deploys it with
the settings in `IaC/6_lambda/config/prod.yaml`. There is no CI/CD pipeline; deployment is a
`terraform apply` in `IaC/6_lambda/` by the infrastructure administrator.

### 4.2 Before every deploy: run the offline checks

The risk agent loads `playbook_rules.json` from its own package, so a copy sits beside its
handler (`Backend/lambdas/agent_risk_clause/playbook_rules.json`). `build_rules.py` writes that
copy and the source together, and both are committed. Before deploying, run:

```bash
python Backend/tests/test_playbook_rules_in_sync.py   # the agent ships the current rules
python Backend/tests/test_extraction_batch.py         # extraction batch handling
```

Both run offline in seconds and need no AWS credentials. Verified 1 October 2026: the deployed
package contains rules identical to `Backend/playbook/playbook_rules.json`.

### 4.3 Database changes

The AI schema is created by `001_ai_schema.sql`; `002_risk_category_tracked_term.sql` adds the
`tracked_term` risk category. Both are applied. `005_judy_ai_read_contract_signers.sql` lets
the AI role read `app.contract_signers` (SELECT only) for the access rule; apply it before
deploying the analysis API version that enforces that rule. New migrations go in `Backend/sql/`, are copied
to `IaC/2_data/scripts/`, and are applied by the infrastructure workstream with the master
credentials — the AI role cannot change the schema.

### 4.4 Verifying a deploy

1. In the Lambda console, each function's *Last modified* time matches the deploy.
2. `python Backend/tests/test_analysis_api.py` passes. It drives the API handler against the
   environment database; its re-run check is a dry run, which leaves runs marked `failed` with
   "dry run - agent not invoked". Those rows are expected, not faults.
3. Re-run analysis on a known contract (section 6.2) and confirm all runs succeed.

As of 1 October 2026 the deployed code of all six AI functions is identical to `main`.

---

## 5. Data model

Two schemas share the `ragdb` database. The application owns `app`; the AI owns `judy_ai`.
The split is enforced by database roles: the AI's role is refused if it tries to write
application records.

| Table | Contents |
|---|---|
| `judy_ai.document_extractions` | One row per extraction: status, page count, extracted text (page markdown, tables as markdown tables), BDA output location (`raw_output_s3_key`), timings, error |
| `judy_ai.agent_runs` | One row per agent run: agent, status (`pending`, `running`, `succeeded`, `failed`), model, prompt version, latency, input and output tokens, raw model response, error |
| `judy_ai.contract_risks` | Agent 1 findings: category, severity, title, detail, playbook section, suggested wording, quote, page |
| `judy_ai.template_detections` | Agent 2 document type and the reason |
| `judy_ai.contract_fields` | Agent 2 fields: type, label, signer role, page, position `{x, y, width, height}` as fractions of the page, origin top-left |
| `judy_ai.contract_summaries` | Agent 3 overview, key points, word count |
| `judy_ai.contract_obligations` | Agent 4 obligations: type, owner, description, timing words, due date when written, recurrence, quote, page |
| `judy_ai.v_latest_agent_runs` | View: the latest run per contract and agent |

Re-runs add new rows; nothing is overwritten, so every result stays traceable to the run, model
and prompt version that produced it. The API always serves the latest successful run. There is
no automated purge or retention policy (outside the SOW); records stay until an administrator
removes them.

Useful queries (run as the AI role through the RDS Query Editor or the Data API):

```sql
-- latest state of every agent for one contract
SELECT agent_type, status, completed_at, error_message
  FROM judy_ai.v_latest_agent_runs WHERE contract_id = '<contract id>';

-- failures in the last day
SELECT contract_id, agent_type, started_at, error_message
  FROM judy_ai.agent_runs
 WHERE status = 'failed' AND started_at > now() - interval '1 day';
```

---

## 6. Operations

### 6.1 Daily health check

| Check | Where | Healthy |
|---|---|---|
| Errors on the six AI functions | CloudWatch → Lambda → `Errors` metric | 0 |
| Messages in `rag-app-prod-document-extraction-sqs-dlq` | SQS console | 0 |
| Failed runs | query in section 5 | none, or explained |

There are no CloudWatch alarms on these yet; section 9 recommends adding them.

### 6.2 Re-run analysis for a contract

Use the API, signed in as an application user:

```http
POST /contracts/{contractId}/analysis
{ "agents": ["riskClause", "templatePrepopulation", "summary"] }
```

Omit the body to re-run the three pre-signing agents. Add `"obligationTracking"` for a signed
contract (unsigned contracts are refused with `422 NOT_SIGNED`). The API creates `pending` runs,
invokes the agents asynchronously and returns `202`; results appear in about 10 seconds. A
second request while runs are in flight returns `409` with the runs in progress.

To repeat **extraction** as well (for example after a BDA change), copy the uploaded object onto
itself with new metadata; this fires the upload event and the whole chain:

```bash
aws s3 cp s3://<bucket>/uploads/<key> s3://<bucket>/uploads/<key> \
  --metadata-directive REPLACE --metadata reprocess=1
```

### 6.3 A document in the dead-letter queue

A message reaches the DLQ after three failed extraction attempts of that upload; other uploads
in the same batch are processed and removed from the queue regardless. Read the message (it holds
the S3 key of the upload), find the error in `/aws/lambda/rag-app-prod-document-extraction`,
fix the cause, then redrive the message from the SQS console ("Start DLQ redrive") or re-trigger
the upload as in 6.2. Common causes are in section 7.

### 6.4 Update the playbook

The playbook is Judy's legal content; its wording is copied verbatim and never edited by code.

1. `python Backend/playbook/build_rules.py path/to/new_playbook.docx` regenerates
   `Backend/playbook/playbook_rules.json` and the agent's copy beside its handler, and reports
   possible typos for Judy to confirm rather than silently correcting them.
2. Check the rule count and spot-check clause text against the document.
3. Run `test_playbook_rules_in_sync.py`, deploy, and run
   `python Backend/tests/evaluate_risk_agent.py`: every playbook rule should still be found on
   the test set.

The converter expects the current structure — section, then standard sentence with "Because"
and the objection, then the action (replace, insert, exhibit, approval, escalate). A playbook in
a different structure needs converter changes.

### 6.5 Change the model or a prompt

Set `MODEL_ID` to another Bedrock **inference profile** ID, or edit a prompt in the agent's
`handler.py` and bump `PROMPT_VERSION`. Then run the evaluators in 6.7 before and after and
compare. Every run records its model and prompt version, so old and new results stay
distinguishable.

### 6.6 Obligations missing on a signed contract

Contracts signed before the completion hook existed, or whose agent run failed, have no
obligations. Re-run with `{"agents": ["obligationTracking"]}` (section 6.2).

### 6.7 Validation tools

Run from `Backend/tests/` with AWS credentials for the account:

| Script | Measures |
|---|---|
| `test_playbook_rules_in_sync.py` | Offline: the risk agent's copy of the rules matches the source |
| `test_extraction_batch.py` | Offline: batch handling — parallel processing, a failing upload retried alone |
| `evaluate_risk_agent.py` | Playbook rules found per test document, dated and numeric terms, invented missing-clause findings |
| `evaluate_template_agent.py` | Document type and field placement on the stored BDA fixtures; draws the boxes on page images |
| `evaluate_summary_agent.py` | Key points traced to a sentence, numbers not in the contract, length |
| `evaluate_obligation_agent.py` | Obligations traced to a sentence, expected obligations found, invented dates |
| `test_analysis_api.py` | The API handler against the environment database |
| `demo_end_to_end.py` | The whole path through the live API: upload, analysis, routing, signing, obligations (~70 s; creates a real contract) |

The test set is `Backend/tests/corpus/`: eight documents built from Judy's own templates.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Upload succeeds but analysis never starts; message ends in the DLQ with "No app.contracts row" | The application did not create the contract record at upload | Check `/aws/lambda/rag-app-prod-data-upload` for `[WARN] Failed to insert app.contracts row`; fix, then re-trigger (6.2) |
| Upload "succeeds" but the file never lands in S3 | The presigned upload URL is signed for `Content-Type: application/octet-stream`; any other type gets `403` | Client must send exactly that content type |
| Extraction `AccessDenied` on `data-automation-profile/us.data-automation-v1` in another region | Cross-region BDA profile | Allow the profile ARN with a wildcard region in the extraction role |
| Agent fails with `ValidationException … on-demand throughput isn't supported` | `MODEL_ID` is a bare model ID | Use the inference profile ID `us.amazon.nova-pro-v1:0` |
| Risk agent fails loading rules, or flags against old rules | The agent's copy of `playbook_rules.json` is missing or out of date | `build_rules.py` writes both copies; `test_playbook_rules_in_sync.py` checks them; redeploy |
| API returns `409 ANALYSIS_NOT_READY` with `status: failed` | That agent's last run failed | Error is in `judy_ai.agent_runs.error_message` and the agent's log group; re-run (6.2) |
| A signer's review screen shows no AI results (`404` from the analysis API) | The AI role cannot read `app.contract_signers`; the log says `ACCESS CHECK cannot read app.contract_signers` | Apply `005_judy_ai_read_contract_signers.sql` |
| Fields on the page are missing or misplaced | BDA project lost bounding boxes (public default project in use) | `BDA_PROJECT_ARN` must point to `judy-ai-contract-extraction` |
| No page images for a PDF | Expected: BDA renders page images for Word documents only | The web application shows PDFs with its own viewer |
| Page images stop loading after a while | Presigned URLs expire after 15 minutes | Reload status with `?include=pageImages` (the web application does this automatically) |
| Re-run of obligation tracking fails with `AccessDenied` | analysis-api role lacks `lambda:InvokeFunction` on the agent | All four agents must be in that role's policy (they are as of 30 Sep 2026) |
| An obligation shows timing words but no date | By design: a date is stored only when the document writes one | — |

Every function logs the real exception. Read the log, not a model's description of it.

---

## 8. Security controls

| Control | Evidence (date verified) |
|---|---|
| Sign-in required on every AI route | Request without a token → `401` (1 Oct 2026) |
| Users see only their own contracts' analysis | Uploader or signer only; others and requests without claims get `404` (`test_analysis_api.py`, 1 Oct 2026) |
| Least privilege per function | One IAM role per function; database access through a dedicated secret; agents can read only the BDA output prefix of the bucket; only extraction and analysis-api may invoke agents (1 Oct 2026) |
| AI cannot change application records | `judy_ai_writer` writing to `app.contracts` → `permission denied` (17 Sep 2026) |
| Encryption at rest | Aurora storage and the database secrets encrypted with a customer-managed KMS key; S3 buckets SSE AES-256; SQS queues encrypted (1 Oct 2026) |
| No public storage | S3 public access block on (30 Sep 2026) |
| Documents stay in the account | Extraction, models, storage and API all run in `580118073904`; page images are rendered and served from inside the account |
| No training on Judy's data | Pre-trained Bedrock model only; Bedrock does not use prompts or documents for training; no fine-tuning |
| Playbook not user-facing | The rules ship inside the risk agent's package; only the replacement wording attached to a finding is ever shown |
| AI never edits documents | Suggestions are advisory text in the database; the source document in S3 is never written by the AI |

---

## 9. Performance baseline

Measured from every successful run in the environment, 1 October 2026.

| Stage | Runs | Typical (median) | 90th percentile | Slowest |
|---|---|---|---|---|
| Text extraction (BDA), average 8.2 pages | 20 | 26 s | 31 s | 56 s (23 pages) |
| Risk & clause | 20 | 4.6 s | 7.1 s | 12.2 s |
| Template pre-population | 19 | 3.2 s | 5.2 s | 6.4 s |
| Summary | 15 | 8.6 s | 13.3 s | 16.4 s |
| Obligation tracking | 4 | 7.8 s | 20.3 s | 25.5 s |

The three pre-signing agents run in parallel, so a typical contract is fully analysed about
40–45 seconds after upload (44 s measured end to end through the API on 29 September).
Obligations were stored 8 seconds after the last signature in the recorded end-to-end run.

**Model usage per contract** (averages): risk 10.8k input / 1.1k output tokens; template
10.2k / 0.5k; summary 7.0k / 1.5k; obligations 10.0k / 2.2k. At Nova Pro list prices
($0.80 / $3.20 per million input / output tokens) that is about **$0.05 per contract** for all
four agents, plus BDA extraction at about $0.01 per page (about $0.08 for an average
document). Verify current prices on the AWS pricing pages; Bedrock usage is billed to Judy's
account.

### Capacity notes and recommended changes

| Item | Today | Recommendation | Owner |
|---|---|---|---|
| Extraction batching | **Fixed and deployed 1 Oct 2026.** Up to 10 uploads per invocation are now processed in parallel (a batch takes about as long as one document), and a failing upload is retried alone: the others are removed from the queue first. Verified offline and against the live services. | Optional: enable `ReportBatchItemFailures` on the event source mapping; the handler is correct with or without it | Infrastructure (deploy) |
| Dead-letter retention | **Done 1 Oct 2026:** 14 days, so a failure over a weekend is still there to resolve | — | Infrastructure (done) |
| Playbook rules packaging | **Fixed 1 Oct 2026:** the agent's copy is committed, written by `build_rules.py` with the source, and checked by `test_playbook_rules_in_sync.py` | — | AI (done) |
| Bedrock permission scope | Agent roles may invoke any Bedrock model | Restrict `bedrock:InvokeModel` to the Nova Pro inference profile and its foundation models | Infrastructure |
| Alarms | None | Alarms on Lambda errors for the six AI functions and on DLQ depth > 0 | Infrastructure |
| Model quotas | Not load-tested | Bedrock request and token quotas for Nova Pro are the real ceiling under load; check them in Service Quotas before a large batch | Infrastructure |

---

## 10. Limits of the AI, by design

From the SOW and the client's decisions:

- Text-based PDF and Word documents, up to 20 pages or 10 MB. No OCR or handwriting.
- Four AI capabilities only. No chat, no free-form generation, no contract drafting; the only
  generated wording is the playbook replacement attached to a risk, and it is advisory.
- Template pre-population recognises three types: MSA, purchase agreement and SOW (16 Sep
  2026). Other documents are reported as "not one of the three types" and still get their
  fields placed. Purchase agreement and SOW recognition are untested until samples arrive.
- Obligations are extracted only where clearly stated; dates are never inferred.
- Results can vary slightly between runs on borderline items; each run is stored with its model,
  prompt version and raw output.
- No custom training or fine-tuning.
