"""
agent_risk_clause Lambda function - Agent 1 of 4, pre-signing.

SOW scope, verbatim:
    "Risk and clause analysis agent that reviews supplier contracts to identify
     unusual terms, missing clauses, date mismatches, and compliance gaps based
     on common patterns."

Plus the advisory output from the E-Signature Development section:
    "AI generated suggestions for alternative language for flagged clauses."

Trigger:
    Async invoke from document_extraction once text is available, or from
    analysis-api on a re-run. Payload: {"contract_id": ..., "extraction_id": ...}

Writes judy_ai.agent_runs and judy_ai.contract_risks.

Two analysis passes
-------------------
1. Playbook pass  - the client's 11-section playbook. Does the contract's
   situation trigger a rule's objection condition, or is the standard clause
   missing or altered?
2. Criteria pass  - the broader flagging criteria Eean gave on 2026-09-16:
   anything carrying a date or number (expiry, renewal windows, thresholds),
   and facility/access prerequisites such as badges, supervision, operating
   hours, security clearance, or access to personal information.

Two calls rather than one because a single prompt asked to both check eleven
specific rules and hunt for general patterns does neither well. Each pass has
one job.

Suggested clause language is NEVER written by the model
-------------------------------------------------------
When a playbook rule fires, suggested_language is looked up verbatim from
playbook_rules.json by rule id. The model only decides *which* rule applies.
Letting it paraphrase the client's legal wording would put invented contract
text in front of users under the client's name. Findings with no playbook rule
carry no suggestion, and playbook_section is NULL - so a non-null
playbook_section is the signal that the wording is authoritative.

Model access note
-----------------
Nova must be invoked through an inference profile ("us.amazon.nova-pro-v1:0").
A plain foundation-model ARN fails with ValidationException - that is what broke
prod chat. Bedrock exceptions here are logged explicitly rather than swallowed,
for the same reason.
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
bedrock_runtime = boto3.client("bedrock-runtime", config=retry_config)
rds_data_client = boto3.client("rds-data", config=retry_config)
s3_client = boto3.client("s3")

# --- Environment ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]

# Inference profile id, not a foundation-model ARN. See module docstring.
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "risk-clause-v1")

# The IaC currently zips a single handler file, so the rules cannot be bundled
# yet. Local file first (works once packaging moves to source_dir), S3 fallback
# in the meantime.
RULES_FILENAME = "playbook_rules.json"
RULES_BUCKET = os.environ.get("RULES_BUCKET")
RULES_KEY = os.environ.get("RULES_KEY", "playbook/playbook_rules.json")

MAX_CONTRACT_CHARS = int(os.environ.get("MAX_CONTRACT_CHARS", "120000"))

VALID_CATEGORIES = {"unusual_term", "missing_clause", "date_mismatch", "compliance_gap"}
VALID_SEVERITIES = {"high", "medium", "low"}


def load_rules():
    """Load the playbook rules once per cold start."""
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), RULES_FILENAME)
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as fh:
            return json.load(fh)

    if RULES_BUCKET:
        body = s3_client.get_object(Bucket=RULES_BUCKET, Key=RULES_KEY)["Body"].read()
        return json.loads(body)

    raise RuntimeError(
        "playbook_rules.json not found next to the handler and RULES_BUCKET is unset"
    )


RULES = load_rules()
RULES_BY_ID = {rule["id"]: rule for rule in RULES["rules"]}


# =============================================================
# Aurora (RDS Data API - no VPC required)
# =============================================================
def execute_sql(sql, parameters=None):
    result = rds_data_client.execute_statement(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=AURORA_SECRET_ARN,
        database=AURORA_DATABASE,
        sql=sql,
        parameters=parameters or [],
    )
    return result.get("records", [])


def string_param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}}


def long_param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"longValue": int(value)}}


def fetch_extraction(extraction_id):
    """Return the extracted text for a succeeded extraction."""
    records = execute_sql(
        """
        SELECT extracted_text, status
          FROM judy_ai.document_extractions
         WHERE id = :id::uuid
        """,
        [string_param("id", extraction_id)],
    )
    if not records:
        raise RuntimeError(f"No extraction {extraction_id}")

    text = records[0][0].get("stringValue")
    status = records[0][1].get("stringValue")

    if status != "succeeded":
        raise RuntimeError(f"Extraction {extraction_id} is '{status}', not 'succeeded'")
    if not text or not text.strip():
        raise RuntimeError(f"Extraction {extraction_id} has no text")

    return text


def start_run(contract_id, extraction_id):
    records = execute_sql(
        """
        INSERT INTO judy_ai.agent_runs
            (contract_id, extraction_id, agent_type, status, model_id, prompt_version)
        VALUES
            (:contract_id::uuid, :extraction_id::uuid, 'risk_clause', 'running',
             :model_id, :prompt_version)
        RETURNING id::text
        """,
        [
            string_param("contract_id", contract_id),
            string_param("extraction_id", extraction_id),
            string_param("model_id", MODEL_ID),
            string_param("prompt_version", PROMPT_VERSION),
        ],
    )
    return records[0][0]["stringValue"]


def complete_run(run_id, raw_response, usage, latency_ms):
    execute_sql(
        """
        UPDATE judy_ai.agent_runs
           SET status        = 'succeeded',
               raw_response  = :raw::jsonb,
               input_tokens  = :input_tokens,
               output_tokens = :output_tokens,
               latency_ms    = :latency_ms,
               completed_at  = now()
         WHERE id = :id::uuid
        """,
        [
            string_param("id", run_id),
            string_param("raw", json.dumps(raw_response)),
            long_param("input_tokens", usage.get("inputTokens")),
            long_param("output_tokens", usage.get("outputTokens")),
            long_param("latency_ms", latency_ms),
        ],
    )


def fail_run(run_id, message):
    execute_sql(
        """
        UPDATE judy_ai.agent_runs
           SET status = 'failed', error_message = :error, completed_at = now()
         WHERE id = :id::uuid
        """,
        [string_param("id", run_id), string_param("error", message[:2000])],
    )


def insert_risks(run_id, contract_id, findings):
    """Insert findings one at a time so a single bad row cannot lose the rest."""
    inserted = 0
    for finding in findings:
        try:
            execute_sql(
                """
                INSERT INTO judy_ai.contract_risks
                    (run_id, contract_id, risk_category, severity, title, detail,
                     playbook_section, suggested_language, source_quote, source_page)
                VALUES
                    (:run_id::uuid, :contract_id::uuid, :category, :severity, :title,
                     :detail, :section, :suggested, :quote, :page)
                """,
                [
                    string_param("run_id", run_id),
                    string_param("contract_id", contract_id),
                    string_param("category", finding["risk_category"]),
                    string_param("severity", finding["severity"]),
                    string_param("title", finding["title"][:300]),
                    string_param("detail", finding["detail"]),
                    string_param("section", finding.get("playbook_section")),
                    string_param("suggested", finding.get("suggested_language")),
                    string_param("quote", finding.get("source_quote")),
                    long_param("page", finding.get("source_page")),
                ],
            )
            inserted += 1
        except ClientError as e:
            print(f"Failed to insert finding {finding.get('title')!r}: {e}")
    return inserted


# =============================================================
# Bedrock
# =============================================================
def invoke_model(system_prompt, user_prompt):
    """
    One Converse call returning parsed JSON findings.

    Bedrock errors are printed before re-raising. The prod chat outage was
    invisible precisely because an equivalent exception was swallowed into a
    string and never reached CloudWatch.
    """
    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
    except ClientError as e:
        print(f"BEDROCK ERROR modelId={MODEL_ID}: {e}")
        raise

    text = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )
    return text, response.get("usage", {})


def parse_findings(text):
    """
    Pull the JSON array out of a model response.

    Models wrap JSON in prose or fences often enough that trusting a bare
    json.loads would make the agent flaky for no good reason.
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()

    if not candidate.startswith("["):
        start = candidate.find("[")
        end = candidate.rfind("]")
        if start == -1 or end == -1:
            print(f"No JSON array in model response: {text[:400]}")
            return []
        candidate = candidate[start:end + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"Could not parse model response as JSON ({e}): {candidate[:400]}")
        return []

    return parsed if isinstance(parsed, list) else []


# =============================================================
# Prompts
# =============================================================
def finding_shape(extra_field=None):
    """
    The required JSON shape for findings.

    Any field the model must return has to appear in this schema. Asking for a
    field in prose alone does not work - the first version requested
    playbook_rule_id in the instructions but omitted it here, and the model
    returned every finding without it, so no playbook wording could be attached.
    """
    extra = f'\n  "{extra_field}": <see instructions above>,' if extra_field else ""
    return f"""Return ONLY a JSON array. Each element must be:
{{{extra}
  "risk_category": "unusual_term" | "missing_clause" | "date_mismatch" | "compliance_gap",
  "severity": "high" | "medium" | "low",
  "title": "short label, under 15 words",
  "detail": "what is wrong and why it matters, 1-3 sentences",
  "source_quote": "the exact sentence from the contract that triggered this, copied verbatim, or null",
  "source_page": <page number from the nearest [page N] marker, or null if not tied to a page>
}}
Return [] if there is nothing to report. No prose outside the JSON array."""


def playbook_prompt(contract_text):
    """Pass 1 - check the contract against the client's playbook rules."""
    rule_lines = []
    for rule in RULES["rules"]:
        conditions = [t["condition"] for t in rule["triggers"] if t.get("condition")]
        rule_lines.append(
            f"- id: {rule['id']}\n"
            f"  topic: {rule['section']}\n"
            f"  standard position: {rule['triggers'][0]['default_clause'][:400]}\n"
            f"  raise when: "
            + ("; ".join(conditions) if conditions
               else "the contract engages this topic but departs from the standard position")
        )

    system = (
        "You review supplier contracts against a company's internal contracting playbook. "
        "You identify problems. You never rewrite contract language. "
        "You are precise and you do not pad your output with rules that do not apply."
    )

    user = f"""Below is a contract, followed by playbook rules.

A rule applies ONLY if the contract actually engages that rule's topic - the contract
grants, requires, describes or contemplates the thing the rule is about, and departs from
the standard position.

Critically: if a contract simply has nothing to do with a rule's topic, that is NOT a
finding. An advisory agreement with no facilities work does not "lack a facilities clause";
the topic is irrelevant to it. Silence on an irrelevant topic is normal and must be ignored.
Only report an absent clause where the contract's own subject matter means it should be
there.

Most contracts will match only one or two rules. Returning many findings is a sign you are
reporting irrelevant topics.

Set "playbook_rule_id" to the id of the rule that applies.
Do not invent replacement wording; that is handled elsewhere.

{finding_shape("playbook_rule_id")}

=== PLAYBOOK RULES ===
{chr(10).join(rule_lines)}

=== CONTRACT ===
{contract_text}"""

    return system, user


def criteria_prompt(contract_text):
    """Pass 2 - the client's broader flagging criteria, beyond the playbook."""
    system = (
        "You review supplier contracts for commercial and compliance risk. "
        "You identify problems. You never rewrite contract language."
    )

    user = f"""Review the contract below and flag:

1. Anything carrying a date or a number that someone must act on or track - expiration
   dates, renewal windows, notice periods, commission or fee percentages, monetary
   thresholds, term lengths. Flag dates that conflict with each other as "date_mismatch".
2. Facility and access prerequisites - badge or unescorted access, supervision
   requirements, permitted operating hours, security clearance, background checks, or
   access to personal, health or customer information.
3. Terms that are unusual or one-sided for a supplier agreement.
4. Clauses a contract of this type would normally contain but this one does not.

Do not report ordinary boilerplate that carries no obligation or deadline.

{finding_shape()}

=== CONTRACT ===
{contract_text}"""

    return system, user


# =============================================================
# Normalisation
# =============================================================
def normalise(finding, from_playbook):
    """
    Validate a model finding and attach authoritative playbook wording.

    Returns None if the finding is unusable. suggested_language is copied from
    the playbook file, never from the model.
    """
    category = str(finding.get("risk_category", "")).strip().lower()
    severity = str(finding.get("severity", "")).strip().lower()

    if category not in VALID_CATEGORIES:
        print(f"Dropping finding with invalid category {category!r}: {finding.get('title')!r}")
        return None
    if severity not in VALID_SEVERITIES:
        severity = "medium"

    title = str(finding.get("title") or "").strip()
    detail = str(finding.get("detail") or "").strip()
    if not title or not detail:
        print(f"Dropping finding missing title or detail: {finding}")
        return None

    page = finding.get("source_page")
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    # A finding not tied to a page (a genuinely absent clause) has no page 0.
    if page is not None and page < 1:
        page = None

    row = {
        "risk_category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "source_quote": (str(finding.get("source_quote")).strip() or None)
                        if finding.get("source_quote") else None,
        "source_page": page,
        "playbook_section": None,
        "suggested_language": None,
    }

    if from_playbook:
        rule = RULES_BY_ID.get(finding.get("playbook_rule_id"))
        if rule is None:
            print(f"Unknown playbook_rule_id {finding.get('playbook_rule_id')!r} - keeping "
                  f"finding without a suggestion")
        else:
            row["playbook_section"] = rule["id"]
            row["suggested_language"] = playbook_suggestion(rule)

    return row


def playbook_suggestion(rule):
    """
    Build the suggestion for a rule from its own recorded actions, verbatim.

    Replacement and insertion text is reproduced exactly. Attachments, approvals
    and legal escalation are appended as short instructions so the reviewer sees
    the whole remedy, not just the wording.
    """
    parts = []
    for action in rule.get("actions", []):
        kind = action.get("type")
        text = (action.get("text") or "").strip()

        if kind in ("replace", "insert") and text:
            parts.append(text)
        elif kind == "attach_exhibit" and text:
            parts.append(f"Also attach: {text}.")
        elif kind == "require_approval":
            parts.append("Business stakeholder approval is required.")
        elif kind == "escalate_to_legal":
            parts.append("Refer to a legal professional before signing.")
        elif kind == "cross_reference" and action.get("note"):
            parts.append(f"Note: {action['note'].strip()}")

    return "\n\n".join(parts) if parts else None


# =============================================================
# Handler
# =============================================================
def analyse(contract_id, extraction_id):
    contract_text = fetch_extraction(extraction_id)
    if len(contract_text) > MAX_CONTRACT_CHARS:
        print(f"Contract text {len(contract_text)} chars, truncating to {MAX_CONTRACT_CHARS}")
        contract_text = contract_text[:MAX_CONTRACT_CHARS]

    run_id = start_run(contract_id, extraction_id)
    print(f"Run {run_id} started for contract {contract_id}")

    started = time.time()
    try:
        raw = {}
        usage_total = {"inputTokens": 0, "outputTokens": 0}
        findings = []

        for pass_name, builder, from_playbook in (
            ("playbook", playbook_prompt, True),
            ("criteria", criteria_prompt, False),
        ):
            system, user = builder(contract_text)
            text, usage = invoke_model(system, user)
            raw[pass_name] = text
            usage_total["inputTokens"] += usage.get("inputTokens", 0)
            usage_total["outputTokens"] += usage.get("outputTokens", 0)

            parsed = parse_findings(text)
            print(f"{pass_name} pass returned {len(parsed)} finding(s)")

            for item in parsed:
                row = normalise(item, from_playbook)
                if row:
                    findings.append(row)

        latency_ms = int((time.time() - started) * 1000)
        inserted = insert_risks(run_id, contract_id, findings)
        complete_run(run_id, raw, usage_total, latency_ms)

        print(f"Run {run_id} succeeded: {inserted} risk(s) stored in {latency_ms} ms")
        return {"run_id": run_id, "risks": inserted}

    except Exception as e:
        print(f"Run {run_id} failed: {e}")
        fail_run(run_id, str(e))
        raise


def lambda_handler(event, context):
    print(f"Event received: {json.dumps(event)[:500]}")

    contract_id = event.get("contract_id")
    extraction_id = event.get("extraction_id")

    if not contract_id or not extraction_id:
        raise ValueError("contract_id and extraction_id are required")

    return analyse(contract_id, extraction_id)
