"""
analysis_api Lambda function - the AI endpoints the Judy interface calls.

SOW scope, verbatim:
    "API endpoints for triggering AI analysis and retrieving results for
     each agent capability."

Routes (API Gateway HTTP API v2, same Cognito JWT authorizer as /upload):
    POST /contracts/{contractId}/analysis              re-run analysis
    GET  /contracts/{contractId}/analysis              status per agent
    GET  /contracts/{contractId}/analysis/{agentType}  results for one agent

The contract for every shape is Docs/AI_AGENTS_API.md. JSON is camelCase,
the database is snake_case, and the mapping tables in that document are
authoritative - the maps at the top of this file mirror them.

This function only reads and writes Postgres and fires asynchronous
invokes. It never runs a model, so every response returns well inside the
29-second API Gateway ceiling. Analysis starts automatically on upload
(document_extraction invokes the agents); the POST here exists for re-runs.

Re-runs and run ids
-------------------
On POST this function inserts one agent_runs row per agent with status
'pending' and passes its id to the agent, so the response can carry runIds
the UI can poll. The agents accept an optional run_id and flip that row to
'running' instead of inserting their own; an agent deployed without that
support inserts a second row and the pending one is left behind, which
the status query tolerates by always reporting the newest row per agent.
"""

import json
import os
import re
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# --- Clients ---
retry_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})
rds_data_client = boto3.client("rds-data", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)

# --- Environment ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]

# Function ARNs (or names) of the agent workers. Any that is unset simply
# cannot be re-run through the API - it returns 422 rather than failing.
AGENT_FUNCTIONS = {
    "riskClause": os.environ.get("RISK_AGENT_ARN"),
    "templatePrepopulation": os.environ.get("TEMPLATE_AGENT_ARN"),
    "summary": os.environ.get("SUMMARY_AGENT_ARN"),
    "obligationTracking": os.environ.get("OBLIGATION_AGENT_ARN"),
}

# A run still 'pending' or 'running' after this long is treated as dead, so
# a crashed agent cannot block re-runs forever. Agents time out at 300 s.
IN_FLIGHT_WINDOW_SECONDS = int(os.environ.get("IN_FLIGHT_WINDOW_SECONDS", "600"))

# Tests set this to exercise the POST path without firing real agents.
DRY_RUN = os.environ.get("ANALYSIS_API_DRY_RUN") == "1"

# =============================================================
# Name mappings - mirror the tables in Docs/AI_AGENTS_API.md
# =============================================================
API_TO_DB_AGENT = {
    "riskClause": "risk_clause",
    "templatePrepopulation": "template_prepopulation",
    "summary": "summary",
    "obligationTracking": "obligation_tracking",
}
DB_TO_API_AGENT = {v: k for k, v in API_TO_DB_AGENT.items()}
ROUTE_TO_API_AGENT = {
    "risk-clause": "riskClause",
    "template-prepopulation": "templatePrepopulation",
    "summary": "summary",
    "obligation-tracking": "obligationTracking",
}
PRE_SIGNING_AGENTS = ("riskClause", "templatePrepopulation", "summary")

RISK_CATEGORY = {
    "unusual_term": "unusualTerm", "missing_clause": "missingClause",
    "date_mismatch": "dateMismatch", "compliance_gap": "complianceGap",
    "tracked_term": "trackedTerm",
}
TEMPLATE_TYPE = {"purchase_agreement": "purchaseAgreement"}   # others are already one word
FIELD_TYPE = {"full_name": "fullName"}
RECURRENCE = {"one_time": "oneTime"}

# Formats a timestamptz as ISO 8601 UTC in SQL, so no timezone handling here.
ISO = "to_char({col} at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"

UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


# =============================================================
# Aurora (RDS Data API - no VPC required)
# =============================================================
def query(sql, parameters=None):
    """Run a parameterized statement and return rows as dicts."""
    result = rds_data_client.execute_statement(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=AURORA_SECRET_ARN,
        database=AURORA_DATABASE,
        sql=sql,
        parameters=parameters or [],
        formatRecordsAs="JSON",
    )
    return json.loads(result.get("formattedRecords") or "[]")


def p(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}}


# =============================================================
# HTTP helpers
# =============================================================
def respond(http_status, body):
    return {
        "statusCode": http_status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def error(http_status, code, message, **extra):
    """Error envelope per Docs/AI_AGENTS_API.md section 6. extra may carry `status`."""
    body = {"error": {"code": code, "message": message, **extra}}
    return respond(http_status, body)


def api_status(db_status):
    return db_status if db_status in ("pending", "running", "succeeded", "failed") else "notStarted"


# =============================================================
# Reads
# =============================================================
def get_contract(contract_id):
    rows = query(
        "SELECT id::text AS id, status FROM app.contracts WHERE id = :id::uuid",
        [p("id", contract_id)],
    )
    return rows[0] if rows else None


def latest_extraction(contract_id):
    rows = query(f"""
        SELECT id::text AS id, status, page_count,
               {ISO.format(col='completed_at')} AS completed_at,
               error_message
          FROM judy_ai.document_extractions
         WHERE contract_id = :cid::uuid
         ORDER BY started_at DESC LIMIT 1
    """, [p("cid", contract_id)])
    return rows[0] if rows else None


def latest_runs(contract_id):
    """Newest run per agent type, whatever its status."""
    return query(f"""
        SELECT DISTINCT ON (agent_type)
               id::text AS id, agent_type, status, error_message,
               {ISO.format(col='completed_at')} AS completed_at,
               {ISO.format(col='started_at')} AS started_at,
               EXTRACT(EPOCH FROM (now() - started_at))::int AS age_seconds
          FROM judy_ai.agent_runs
         WHERE contract_id = :cid::uuid
         ORDER BY agent_type, started_at DESC
    """, [p("cid", contract_id)])


def latest_succeeded_run(contract_id, db_agent, run_id=None):
    if run_id:
        rows = query(f"""
            SELECT id::text AS id, agent_type, status, {ISO.format(col='completed_at')} AS completed_at
              FROM judy_ai.agent_runs
             WHERE id = :rid::uuid AND contract_id = :cid::uuid AND agent_type = :agent
        """, [p("rid", run_id), p("cid", contract_id), p("agent", db_agent)])
    else:
        rows = query(f"""
            SELECT id::text AS id, agent_type, status, {ISO.format(col='completed_at')} AS completed_at
              FROM judy_ai.agent_runs
             WHERE contract_id = :cid::uuid AND agent_type = :agent AND status = 'succeeded'
             ORDER BY started_at DESC LIMIT 1
        """, [p("cid", contract_id), p("agent", db_agent)])
    return rows[0] if rows else None


def result_count(db_agent, run_id):
    table = {
        "risk_clause": "SELECT count(*) AS n FROM judy_ai.contract_risks WHERE run_id = :rid::uuid",
        "template_prepopulation": "SELECT count(*) AS n FROM judy_ai.contract_fields WHERE run_id = :rid::uuid",
        "summary": "SELECT coalesce(jsonb_array_length(key_points), 0) AS n FROM judy_ai.contract_summaries WHERE run_id = :rid::uuid",
        "obligation_tracking": "SELECT count(*) AS n FROM judy_ai.contract_obligations WHERE run_id = :rid::uuid",
    }[db_agent]
    rows = query(table, [p("rid", run_id)])
    return int(rows[0]["n"]) if rows else 0


# =============================================================
# GET /contracts/{id}/analysis
# =============================================================
def handle_status(contract_id):
    contract = get_contract(contract_id)
    if not contract:
        return error(404, "CONTRACT_NOT_FOUND", f"Unknown contract {contract_id}")

    extraction = latest_extraction(contract_id)
    runs = {r["agent_type"]: r for r in latest_runs(contract_id)}

    agents = {}
    for api_name, db_name in API_TO_DB_AGENT.items():
        run = runs.get(db_name)
        if not run:
            agents[api_name] = {"runId": None, "status": "notStarted",
                                "completedAt": None, "resultCount": None}
            continue
        entry = {
            "runId": run["id"],
            "status": api_status(run["status"]),
            "completedAt": run.get("completed_at"),
            "resultCount": result_count(db_name, run["id"]) if run["status"] == "succeeded" else None,
        }
        if run["status"] == "failed" and run.get("error_message"):
            entry["error"] = run["error_message"][:300]
        agents[api_name] = entry

    return respond(200, {
        "contractId": contract_id,
        "contractStatus": contract["status"],
        "extraction": {
            "status": extraction["status"] if extraction else "notStarted",
            "pageCount": extraction.get("page_count") if extraction else None,
            "completedAt": extraction.get("completed_at") if extraction else None,
            **({"error": extraction["error_message"][:300]}
               if extraction and extraction.get("error_message") else {}),
        },
        "agents": agents,
    })


# =============================================================
# GET /contracts/{id}/analysis/{agentType}
# =============================================================
def risks_for(run_id):
    rows = query("""
        SELECT id::text AS id, risk_category, severity, title, detail, playbook_section,
               suggested_language, source_quote, source_page
          FROM judy_ai.contract_risks WHERE run_id = :rid::uuid
         ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  risk_category, source_page NULLS LAST, title
    """, [p("rid", run_id)])
    return [{
        "id": r["id"],
        "category": RISK_CATEGORY.get(r["risk_category"], r["risk_category"]),
        "severity": r["severity"],
        "title": r["title"],
        "detail": r["detail"],
        "playbookSection": r.get("playbook_section"),
        "suggestedLanguage": r.get("suggested_language"),
        "sourceQuote": r.get("source_quote"),
        "sourcePage": r.get("source_page"),
    } for r in rows]


def fields_for(run_id):
    detection = query("""
        SELECT detected_template_type, rationale
          FROM judy_ai.template_detections WHERE run_id = :rid::uuid
    """, [p("rid", run_id)])
    rows = query("""
        SELECT id::text AS id, field_type, label, signer_role, page, position, is_required
          FROM judy_ai.contract_fields WHERE run_id = :rid::uuid
         ORDER BY page NULLS LAST, (position->>'y')::float NULLS LAST, (position->>'x')::float
    """, [p("rid", run_id)])
    det = detection[0] if detection else {}
    template_type = det.get("detected_template_type") or "unknown"
    return {
        "detectedTemplateType": TEMPLATE_TYPE.get(template_type, template_type),
        "rationale": det.get("rationale"),
        "fields": [{
            "id": r["id"],
            "fieldType": FIELD_TYPE.get(r["field_type"], r["field_type"]),
            "label": r.get("label"),
            "signerRole": r.get("signer_role"),
            "page": r.get("page"),
            "position": r.get("position"),   # already {x, y, width, height} JSON
            "isRequired": bool(r.get("is_required", True)),
        } for r in rows],
    }


def summary_for(run_id):
    rows = query("""
        SELECT summary_text, key_points, word_count
          FROM judy_ai.contract_summaries WHERE run_id = :rid::uuid
    """, [p("rid", run_id)])
    if not rows:
        return {"summaryText": None, "keyPoints": [], "wordCount": None}
    s = rows[0]
    key_points = s.get("key_points") or []
    if isinstance(key_points, str):
        key_points = json.loads(key_points)
    return {"summaryText": s["summary_text"], "keyPoints": key_points, "wordCount": s.get("word_count")}


def obligations_for(run_id):
    rows = query("""
        SELECT id::text AS id, obligation_type, description, owner_party,
               to_char(due_date, 'YYYY-MM-DD') AS due_date, raw_date_text, recurrence,
               source_quote, source_page
          FROM judy_ai.contract_obligations WHERE run_id = :rid::uuid
         ORDER BY due_date NULLS LAST, obligation_type
    """, [p("rid", run_id)])
    return [{
        "id": r["id"],
        "obligationType": r["obligation_type"],
        "description": r["description"],
        "ownerParty": r.get("owner_party"),
        "dueDate": r.get("due_date"),
        "rawDateText": r.get("raw_date_text"),
        "recurrence": RECURRENCE.get(r.get("recurrence"), r.get("recurrence")),
        "sourceQuote": r.get("source_quote"),
        "sourcePage": r.get("source_page"),
    } for r in rows]


def handle_results(contract_id, route_agent, run_id):
    api_agent = ROUTE_TO_API_AGENT.get(route_agent)
    if not api_agent:
        return error(404, "UNKNOWN_AGENT",
                     f"agentType must be one of {', '.join(ROUTE_TO_API_AGENT)}")
    db_agent = API_TO_DB_AGENT[api_agent]

    contract = get_contract(contract_id)
    if not contract:
        return error(404, "CONTRACT_NOT_FOUND", f"Unknown contract {contract_id}")

    if run_id and not UUID_RE.match(run_id):
        return error(400, "BAD_REQUEST", "runId must be a UUID")

    run = latest_succeeded_run(contract_id, db_agent, run_id)
    if not run or run["status"] != "succeeded":
        latest = {r["agent_type"]: r for r in latest_runs(contract_id)}.get(db_agent)
        current = api_status(latest["status"]) if latest else "notStarted"
        label = {"riskClause": "Risk & clause", "templatePrepopulation": "Template pre-population",
                 "summary": "Summary", "obligationTracking": "Obligation tracking"}[api_agent]
        return error(409, "ANALYSIS_NOT_READY",
                     f"{label} agent has not completed for this contract.", status=current)

    body = {
        "runId": run["id"],
        "agentType": api_agent,
        "status": "succeeded",
        "completedAt": run.get("completed_at"),
    }
    if db_agent == "risk_clause":
        body["risks"] = risks_for(run["id"])
    elif db_agent == "template_prepopulation":
        body.update(fields_for(run["id"]))
    elif db_agent == "summary":
        body.update(summary_for(run["id"]))
    else:
        body["obligations"] = obligations_for(run["id"])
    return respond(200, body)


# =============================================================
# POST /contracts/{id}/analysis
# =============================================================
def parse_agents(raw_body):
    """Which agents to run. Empty body means the three pre-signing agents."""
    if not raw_body:
        return list(PRE_SIGNING_AGENTS), None
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return None, "Request body must be JSON"
    wanted = body.get("agents") if isinstance(body, dict) else None
    if wanted is None:
        return list(PRE_SIGNING_AGENTS), None
    if not isinstance(wanted, list) or not wanted:
        return None, "agents must be a non-empty list"
    unknown = [a for a in wanted if a not in API_TO_DB_AGENT]
    if unknown:
        return None, f"Unknown agents: {', '.join(map(str, unknown))}"
    return list(dict.fromkeys(wanted)), None


def create_pending_run(contract_id, extraction_id, db_agent):
    rows = query("""
        INSERT INTO judy_ai.agent_runs (contract_id, extraction_id, agent_type, status)
        VALUES (:cid::uuid, :eid::uuid, :agent, 'pending')
        RETURNING id::text AS id
    """, [p("cid", contract_id), p("eid", extraction_id), p("agent", db_agent)])
    return rows[0]["id"]


def fail_run(run_id, message):
    query("""
        UPDATE judy_ai.agent_runs
           SET status = 'failed', error_message = :err, completed_at = now()
         WHERE id = :rid::uuid
    """, [p("rid", run_id), p("err", message[:2000])])


def handle_trigger(contract_id, raw_body):
    contract = get_contract(contract_id)
    if not contract:
        return error(404, "CONTRACT_NOT_FOUND", f"Unknown contract {contract_id}")

    agents, problem = parse_agents(raw_body)
    if problem:
        return error(400, "BAD_REQUEST", problem)

    extraction = latest_extraction(contract_id)
    if not extraction or extraction["status"] != "succeeded":
        state = extraction["status"] if extraction else "notStarted"
        return error(422, "EXTRACTION_FAILED",
                     "Document has no successful extraction and cannot be analyzed.",
                     extractionStatus=state)

    if "obligationTracking" in agents and contract["status"] != "signed":
        return error(422, "NOT_SIGNED",
                     "Obligation tracking runs on signed contracts only.",
                     contractStatus=contract["status"])

    unavailable = [a for a in agents if not AGENT_FUNCTIONS.get(a) and not DRY_RUN]
    if unavailable:
        return error(422, "AGENT_UNAVAILABLE",
                     f"Not deployed yet: {', '.join(unavailable)}")

    # Refuse to stack runs: anything pending/running recently for a requested
    # agent means one is already on its way.
    in_flight = [
        r for r in latest_runs(contract_id)
        if r["status"] in ("pending", "running")
        and int(r.get("age_seconds") or 0) < IN_FLIGHT_WINDOW_SECONDS
        and DB_TO_API_AGENT.get(r["agent_type"]) in agents
    ]
    if in_flight:
        return error(409, "ANALYSIS_IN_PROGRESS",
                     "Analysis is already running for this contract.",
                     runs=[{"runId": r["id"], "agentType": DB_TO_API_AGENT[r["agent_type"]],
                            "status": api_status(r["status"])} for r in in_flight])

    runs = []
    payload_base = {"contract_id": contract_id, "extraction_id": extraction["id"],
                    "trigger": "api_rerun"}
    for api_agent in agents:
        db_agent = API_TO_DB_AGENT[api_agent]
        run_id = create_pending_run(contract_id, extraction["id"], db_agent)
        status = "pending"
        if DRY_RUN:
            fail_run(run_id, "dry run - agent not invoked")
            status = "failed"
        else:
            try:
                lambda_client.invoke(
                    FunctionName=AGENT_FUNCTIONS[api_agent],
                    InvocationType="Event",
                    Payload=json.dumps({**payload_base, "run_id": run_id}).encode(),
                )
                print(f"Invoked {api_agent} for contract {contract_id} as run {run_id}")
            except ClientError as e:
                print(f"Failed to invoke {api_agent}: {e}")
                fail_run(run_id, f"Could not start agent: {e}")
                status = "failed"
        runs.append({"runId": run_id, "agentType": api_agent, "status": status})

    return respond(202, {"contractId": contract_id, "extractionId": extraction["id"], "runs": runs})


# =============================================================
# Entry point
# =============================================================
def lambda_handler(event, context):
    route_key = event.get("routeKey", "")
    params = event.get("pathParameters") or {}
    contract_id = params.get("contractId", "")
    started = time.time()
    print(f"{route_key} contract={contract_id}")

    if not UUID_RE.match(contract_id or ""):
        return error(400, "BAD_REQUEST", "contractId must be a UUID")

    try:
        if route_key == "POST /contracts/{contractId}/analysis":
            out = handle_trigger(contract_id, event.get("body"))
        elif route_key == "GET /contracts/{contractId}/analysis":
            out = handle_status(contract_id)
        elif route_key == "GET /contracts/{contractId}/analysis/{agentType}":
            run_id = (event.get("queryStringParameters") or {}).get("runId")
            out = handle_results(contract_id, params.get("agentType", ""), run_id)
        else:
            out = error(404, "UNKNOWN_ROUTE", f"Unsupported route: {route_key}")
    except ClientError as e:
        # Database or invoke errors are the only expected failures here; log the
        # real exception, never a paraphrase of it.
        print(f"AWS ERROR on {route_key}: {e}")
        out = error(500, "INTERNAL_ERROR", "The analysis service could not complete the request.")

    print(f"{route_key} -> {out['statusCode']} in {int((time.time() - started) * 1000)} ms")
    return out
