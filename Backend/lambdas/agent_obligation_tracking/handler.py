"""
agent_obligation_tracking Lambda function - Agent 4 of 4, post-signing.

SOW scope, verbatim:
    "Obligation tracking agent that extracts vendor commitments, renewal
     dates, and contract milestones where clearly stated in document text,
     returning results in structured format."

Acceptance criterion 4: "Signed contracts are processed to extract
obligations, renewal dates, and vendor commitments where clearly stated in
document text." So this agent runs on signed contracts only.

Trigger:
    Async invoke from the signature workflow when the last signer confirms
    (payload {"contract_id": ..., "trigger": "signature_completed"}), or from
    analysis-api on a re-run ({"contract_id", "extraction_id", "run_id",
    "trigger": "api_rerun"}). When no extraction_id is given, the newest
    succeeded extraction for the contract is used - the signed document is
    the one that was analysed before signing.

Writes judy_ai.agent_runs and judy_ai.contract_obligations.

"Where clearly stated" is the guardrail
--------------------------------------
The model returns each obligation with the exact sentence it came from and
its page. An obligation without a quotable sentence is dropped. Dates are
kept as a pair: due_date only when the document states a calendar date
(or one can be computed from a stated date plus a stated period), and
raw_date_text always carrying the document's own words. "Within 30 days of
the Effective Date" with no Effective Date filled in is stored with a null
due_date and the wording - the UI shows the wording. This is the behaviour
agreed with the application team on 2026-09-18.

Model access note
-----------------
Nova must be invoked through an inference profile ("us.amazon.nova-pro-v1:0").
Bedrock exceptions are logged explicitly rather than swallowed.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# --- Clients ---
retry_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})
bedrock_runtime = boto3.client("bedrock-runtime", config=retry_config)
rds_data_client = boto3.client("rds-data", config=retry_config)

# --- Environment ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]

MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "obligation-tracking-v1")
MAX_CONTRACT_CHARS = int(os.environ.get("MAX_CONTRACT_CHARS", "120000"))

# Set to "1" to run on a contract that is not yet signed (local evaluation,
# or a deliberate pre-signing preview). Never set in the deployed function.
ALLOW_UNSIGNED = os.environ.get("ALLOW_UNSIGNED") == "1"

OBLIGATION_TYPES = ("commitment", "renewal", "milestone", "other")
RECURRENCES = ("one_time", "monthly", "quarterly", "annual")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def date_param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}, "typeHint": "DATE"}


def contract_status(contract_id):
    records = execute_sql(
        "SELECT status, signed_at::text FROM app.contracts WHERE id = :id::uuid",
        [string_param("id", contract_id)],
    )
    if not records:
        raise RuntimeError(f"No contract {contract_id}")
    return records[0][0].get("stringValue"), records[0][1].get("stringValue")


def latest_extraction_id(contract_id):
    records = execute_sql(
        """
        SELECT id::text FROM judy_ai.document_extractions
         WHERE contract_id = :cid::uuid AND status = 'succeeded'
         ORDER BY started_at DESC LIMIT 1
        """,
        [string_param("cid", contract_id)],
    )
    if not records:
        raise RuntimeError(f"Contract {contract_id} has no succeeded extraction")
    return records[0][0]["stringValue"]


def fetch_extraction(extraction_id):
    records = execute_sql(
        """
        SELECT extracted_text, status FROM judy_ai.document_extractions WHERE id = :id::uuid
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


def start_run(contract_id, extraction_id, run_id=None):
    """Create the run row, or claim the 'pending' one the analysis API created."""
    if run_id:
        records = execute_sql(
            """
            UPDATE judy_ai.agent_runs
               SET status = 'running', model_id = :model_id, prompt_version = :prompt_version,
                   started_at = now()
             WHERE id = :run_id::uuid AND agent_type = 'obligation_tracking'
            RETURNING id::text
            """,
            [
                string_param("run_id", run_id),
                string_param("model_id", MODEL_ID),
                string_param("prompt_version", PROMPT_VERSION),
            ],
        )
        if records:
            return records[0][0]["stringValue"]
        print(f"run_id {run_id} not found for this agent - inserting a new run")

    records = execute_sql(
        """
        INSERT INTO judy_ai.agent_runs
            (contract_id, extraction_id, agent_type, status, model_id, prompt_version)
        VALUES
            (:contract_id::uuid, :extraction_id::uuid, 'obligation_tracking', 'running',
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


def insert_obligations(run_id, contract_id, obligations):
    """Insert one at a time so a single bad row cannot lose the rest."""
    inserted = 0
    for o in obligations:
        try:
            execute_sql(
                """
                INSERT INTO judy_ai.contract_obligations
                    (run_id, contract_id, obligation_type, description, owner_party,
                     due_date, raw_date_text, recurrence, source_quote, source_page)
                VALUES
                    (:run_id::uuid, :contract_id::uuid, :type, :description, :owner,
                     :due_date, :raw_date, :recurrence, :quote, :page)
                """,
                [
                    string_param("run_id", run_id),
                    string_param("contract_id", contract_id),
                    string_param("type", o["obligation_type"]),
                    string_param("description", o["description"]),
                    string_param("owner", o.get("owner_party")),
                    date_param("due_date", o.get("due_date")),
                    string_param("raw_date", o.get("raw_date_text")),
                    string_param("recurrence", o.get("recurrence")),
                    string_param("quote", o.get("source_quote")),
                    long_param("page", o.get("source_page")),
                ],
            )
            inserted += 1
        except ClientError as e:
            print(f"Failed to insert obligation {o.get('description')!r}: {e}")
    return inserted


# =============================================================
# Bedrock
# =============================================================
def invoke_model(system_prompt, user_prompt):
    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 5000, "temperature": 0},
        )
    except ClientError as e:
        print(f"BEDROCK ERROR modelId={MODEL_ID}: {e}")
        raise
    text = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
    return text, response.get("usage", {})


def parse_array(text):
    """
    Pull the JSON array out of a model response.

    If the response was cut off by the output-token limit mid-array (a long
    MSA produced exactly that on the first corpus run), the complete objects
    before the cut are salvaged rather than the whole result being lost.
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    start = candidate.find("[")
    if start == -1:
        print(f"No JSON array in model response: {text[:400]}")
        return []
    candidate = candidate[start:]
    end = candidate.rfind("]")
    if end != -1:
        try:
            parsed = json.loads(candidate[:end + 1])
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            pass

    # Truncated: keep every complete top-level object.
    salvaged = []
    depth = 0
    obj_start = None
    in_string = False
    escape = False
    for i, ch in enumerate(candidate):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    salvaged.append(json.loads(candidate[obj_start:i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    print(f"Model response was truncated; salvaged {len(salvaged)} complete obligation(s)")
    return salvaged


# =============================================================
# Prompt
# =============================================================
SYSTEM_PROMPT = (
    "You extract obligations from signed contracts for a tracking dashboard. You report "
    "only what the document clearly states, each item tied to the sentence it comes from. "
    "You never infer a date the document does not give, and you never give advice."
)

OUTPUT_SHAPE = """Return ONLY a JSON array. Each element must be:
{
  "obligation_type": "commitment" | "renewal" | "milestone" | "other",
  "description": "one plain sentence a reader could act on, under 30 words",
  "owner_party": "lower-case party or null",
  "due_date": "YYYY-MM-DD" or null,
  "raw_date_text": "the document's own words for the timing, or null if it states none",
  "recurrence": "one_time" | "monthly" | "quarterly" | "annual" | null,
  "source_quote": "the exact sentence from the contract, copied verbatim, at most 200 characters",
  "source_page": <integer>
}
Return [] if there is nothing to report. No prose outside the JSON array."""

DATE_RULES = """- Dates come as a PAIR. "raw_date_text" is always the document's own words for the timing
  ("within 30 days after being incurred", "90 days prior to the end of the then-current term",
  "1 March 2026"), never the whole sentence, and null when the sentence states no timing.
  "due_date" is a calendar date in YYYY-MM-DD ONLY when the document writes that date out.
  Never compute a date from a period and a start date - "three years from 1 March 2026" has
  due_date null and raw_date_text "three years from 1 March 2026". A blank ("[__________]")
  means null. Never guess.
- "owner_party" is who owes the obligation, in the document's own terms, lower case:
  "supplier", "vendor", "company", "customer", "advisor", "both", or null if unclear.
- "recurrence": "one_time" | "monthly" | "quarterly" | "annual", or null when the document does
  not say the obligation repeats.
- Page numbers come from the nearest [page N] marker above the sentence.
- Each item MUST quote the exact sentence it comes from. If you cannot quote a sentence, do
  not report it."""


def signed_line(signed_on):
    return (
        f"The contract was signed on {signed_on}. Use that only to resolve a period the document "
        "ties to signing or to its effective date when the effective date is the signing date."
        if signed_on else
        "The signing date is not known; do not assume one."
    )


def numeric_prompt(contract_text, signed_on):
    """
    Pass 1 - every term carrying a date, period, amount, rate or threshold.

    A single "list all obligations" prompt walks a 20-page MSA top-down and
    fills its output with generic duties before reaching the commission
    schedule on page 9. Asking for the numeric terms alone is the same
    narrow job the risk agent's criteria pass does, and it finds them.
    """
    user = f"""{signed_line(signed_on)}

Go through the ENTIRE signed contract below, including tables, exhibits and order forms at
the end, and report every sentence that carries a date, a period, a deadline, an amount, a
percentage, a rate or a threshold that someone must track after signing. Nothing else.

Examples of what counts: the term and when it ends; automatic renewal periods; the window for
notice of non-renewal; the notice period to terminate; termination if something does not
happen within a period; invoice or payment deadlines; late-payment interest; fee increases
and when they apply; commission or royalty rates, including ones that step up at an
anniversary (ONE item stating both values and when it changes); monetary thresholds such as
an amount above which a purchase order or approval is required; delivery, report, audit or
review deadlines; restrictions with a stated period (non-solicitation for one year).

Label each: "renewal" for anything about the life of the agreement (term, renewal, notice to
terminate or not renew, price changes at renewal); "milestone" for a dated point in time or a
termination tied to a period of inactivity; "commitment" for a payment, rate, delivery or
notification with a deadline or amount; "other" if none fit.

Rules:
{DATE_RULES}
- Report at most 30 items. Skip a sentence that repeats an item already reported.

{OUTPUT_SHAPE}

=== CONTRACT ===
{contract_text}"""
    return SYSTEM_PROMPT, user


def duties_prompt(contract_text, signed_on):
    """Pass 2 - concrete deliverables and recurring duties that carry no number."""
    user = f"""{signed_line(signed_on)}

Go through the signed contract below and report the CONCRETE deliverables and recurring
duties a party must perform after signing that a tracking dashboard should list even though
no date or amount is attached: software or work product to be delivered, training or
workshops to be run, reports to be provided, materials to be returned on termination,
certifications to be given, notices that must be sent when something happens.

Do NOT report general duties: standard of care, "perform professionally", indemnify, keep
confidential, comply with law, cooperate, maintain security, assign intellectual property,
grant a licence, use in accordance with documentation. Those are not trackable items.

Label each "commitment" (or "other" if it does not fit). Report at most 12 items.

Rules:
{DATE_RULES}

{OUTPUT_SHAPE}

=== CONTRACT ===
{contract_text}"""
    return SYSTEM_PROMPT, user



# =============================================================
# Normalisation
# =============================================================
def normalise(item):
    """Validate one obligation. Returns None if it cannot be trusted."""
    if not isinstance(item, dict):
        return None

    otype = str(item.get("obligation_type") or "").strip().lower()
    if otype not in OBLIGATION_TYPES:
        otype = "other"

    description = str(item.get("description") or "").strip()
    quote = str(item.get("source_quote") or "").strip()
    if not description or not quote:
        print(f"Dropping obligation without description or source sentence: {item}")
        return None

    due = item.get("due_date")
    due = str(due).strip() if due else None
    if due and not DATE_RE.match(due):
        print(f"Dropping malformed due_date {due!r} (keeping the wording)")
        due = None
    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            print(f"Dropping impossible due_date {due!r} (keeping the wording)")
            due = None

    raw_date = item.get("raw_date_text")
    raw_date = str(raw_date).strip() if raw_date else None

    recurrence = str(item.get("recurrence") or "").strip().lower() or None
    if recurrence not in RECURRENCES:
        recurrence = None

    page = item.get("source_page")
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    if page is not None and page < 1:
        page = None

    owner = str(item.get("owner_party") or "").strip().lower() or None

    return {
        "obligation_type": otype,
        "description": description[:400],
        "owner_party": owner,
        "due_date": due,
        "raw_date_text": raw_date[:300] if raw_date else None,
        "recurrence": recurrence,
        "source_quote": quote[:600],
        "source_page": page,
    }


# =============================================================
# Handler
# =============================================================
TIMING = re.compile(
    r"\d|\$|%|\b(day|days|week|weeks|month|months|year|years|annual|annually|quarter|quarterly|"
    r"hour|hours|monthly|weekly|anniversary|renew|renewal|expir|deadline|due date|milestone)\b",
    re.IGNORECASE,
)
DELIVERABLE = re.compile(
    r"\b(deliver|delivery|deliverable|provide|submit|invoice|report|pay|refund|return|"
    r"install|deploy|train|workshop|complete|issue|notify|notice)\b",
    re.IGNORECASE,
)
GENERIC_DUTY = re.compile(
    r"comply|compliance|applicable law|indemnif|hold harmless|confidential|standard of care|"
    r"professional|cooperat|represent|warrant|assign(?:s|ment)? .*intellectual|"
    r"intellectual property|in accordance with|good faith|reasonable efforts|"
    r"license to|licen[cs]e|solicit|non-?compete|not (?:disclose|assign|use)",
    re.IGNORECASE,
)


def date_is_written(iso_date, contract_text, signed_on=None):
    """
    True when the document (or the signing date) writes this calendar date out.

    Accepted spellings: 2026-03-01, 1 March 2026, March 1, 2026, 01/03/2026,
    03/01/2026, 1st March 2026, March 1st, 2026. Anything else means the model
    derived the date, and derived dates are not stored.
    """
    if signed_on and iso_date == signed_on:
        return True
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return False
    month = d.strftime("%B")
    mon = d.strftime("%b")
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    forms = [
        iso_date,
        f"{day} {month} {d.year}", f"{day}{suffix} {month} {d.year}",
        f"{month} {day}, {d.year}", f"{month} {day}{suffix}, {d.year}", f"{month} {day} {d.year}",
        f"{day} {mon} {d.year}", f"{mon} {day}, {d.year}", f"{mon}. {day}, {d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{d.day}/{d.month}/{d.year}", f"{d.month}/{d.day}/{d.year}",
        f"{d.day:02d}.{d.month:02d}.{d.year}",
    ]
    text = re.sub(r"\s+", " ", contract_text)
    return any(f in text for f in forms)


def is_trackable(o):
    """
    Keep what a tracking dashboard can act on.

    An obligation stays when its description or timing words carry a
    number, date, period, amount or rate, or when it is a concrete
    deliverable; a generic duty with none of those (comply with law, keep
    confidential, indemnify) is dropped. The SOW asks for "vendor commitments,
    renewal dates, and contract milestones where clearly stated", and the
    client's own test is "anything with a date or a number attached".
    """
    timing_text = f"{o['description']} {o.get('raw_date_text') or ''}"
    if o["obligation_type"] in ("renewal", "milestone"):
        return True
    if TIMING.search(timing_text):
        return True
    if GENERIC_DUTY.search(o["description"]):
        return False
    return bool(DELIVERABLE.search(o["description"]))


def quote_key(quote):
    """Normalised prefix of a quote, for de-duplication across the two passes."""
    return re.sub(r"[^a-z0-9]+", " ", (quote or "").lower()).strip()[:80]


def analyse_text(contract_text, signed_on=None):
    """
    Run both passes concurrently, then normalise, filter and de-duplicate.

    Pure - no database access. Shared with the evaluator so the code that
    runs in Lambda is exactly the code that gets measured.
    Returns (obligations, raw_by_pass, usage_total).
    """
    if len(contract_text) > MAX_CONTRACT_CHARS:
        print(f"Contract text {len(contract_text)} chars, truncating to {MAX_CONTRACT_CHARS}")
        contract_text = contract_text[:MAX_CONTRACT_CHARS]

    passes = (("numeric", numeric_prompt), ("duties", duties_prompt))
    with ThreadPoolExecutor(max_workers=len(passes)) as pool:
        futures = {name: pool.submit(invoke_model, *builder(contract_text, signed_on))
                   for name, builder in passes}
        results = {name: f.result() for name, f in futures.items()}

    raw = {}
    usage_total = {"inputTokens": 0, "outputTokens": 0}
    candidates = []
    for name, _ in passes:
        text, usage = results[name]
        raw[name] = text
        usage_total["inputTokens"] += usage.get("inputTokens", 0)
        usage_total["outputTokens"] += usage.get("outputTokens", 0)
        parsed = parse_array(text)
        print(f"{name} pass returned {len(parsed)} item(s)")
        candidates.extend(o for o in (normalise(i) for i in parsed) if o)

    obligations = []
    seen = set()
    for o in candidates:
        if not is_trackable(o):
            print(f"Dropping generic duty with no date, amount or deliverable: {o['description'][:80]!r}")
            continue
        key = quote_key(o["source_quote"])
        if key in seen:
            continue
        seen.add(key)
        if o["due_date"] and not date_is_written(o["due_date"], contract_text, signed_on):
            # "Where clearly stated": a date the document does not write out was
            # computed by the model, and a computed date can be wrong without
            # anything on the page to check it against. Keep the words.
            print(f"Dropping computed due_date {o['due_date']} for {o['description'][:60]!r}; "
                  f"keeping wording {o.get('raw_date_text')!r}")
            o["due_date"] = None
        obligations.append(o)

    dated = sum(1 for o in obligations if o["due_date"])
    print(f"{len(obligations)} of {len(candidates)} obligation(s) kept, {dated} with a calendar date")
    return obligations, raw, usage_total


def analyse(contract_id, extraction_id=None, run_id=None, trigger=None):
    status, signed_at = contract_status(contract_id)
    if status != "signed" and not ALLOW_UNSIGNED:
        raise RuntimeError(
            f"Contract {contract_id} is '{status}', not 'signed' - obligation tracking runs on "
            f"signed contracts only (trigger={trigger})"
        )

    extraction_id = extraction_id or latest_extraction_id(contract_id)
    contract_text = fetch_extraction(extraction_id)
    run_id = start_run(contract_id, extraction_id, run_id)
    print(f"Run {run_id} started for contract {contract_id} (status {status}, trigger {trigger})")

    started = time.time()
    try:
        signed_on = signed_at[:10] if signed_at else None
        obligations, raw, usage = analyse_text(contract_text, signed_on)
        latency_ms = int((time.time() - started) * 1000)
        inserted = insert_obligations(run_id, contract_id, obligations)
        complete_run(run_id, raw, usage, latency_ms)
        print(f"Run {run_id} succeeded: {inserted} obligation(s) stored in {latency_ms} ms")
        return {"run_id": run_id, "obligations": inserted}
    except Exception as e:
        print(f"Run {run_id} failed: {e}")
        fail_run(run_id, str(e))
        raise


def lambda_handler(event, context):
    print(f"Event received: {json.dumps(event)[:500]}")
    contract_id = event.get("contract_id")
    if not contract_id:
        raise ValueError("contract_id is required")
    return analyse(
        contract_id,
        extraction_id=event.get("extraction_id"),
        run_id=event.get("run_id"),
        trigger=event.get("trigger"),
    )
