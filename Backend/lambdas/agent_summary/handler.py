"""
agent_summary Lambda function - Agent 3 of 4, pre-signing.

SOW scope, verbatim:
    "Plain-language summary generation agent that produces concise overviews
     of lengthy supplier agreements."

Trigger:
    Async invoke from document_extraction once text is available, or from
    analysis-api on a re-run. Payload: {"contract_id": ..., "extraction_id": ...}

Writes judy_ai.agent_runs and judy_ai.contract_summaries.

What this agent is, and is not
------------------------------
It is summarisation, not generation. The SOW rules out free-form content
generation and chat; this agent restates what the document says, in plain
language, for someone who is not a lawyer. It gives no advice, adds no terms,
and does not judge the contract - that is agent 1's job. Everything it says
must be traceable to a sentence in the document.

Traceability is enforced, not hoped for
---------------------------------------
The model returns each key point with the sentence it came from and its
page. The API contract exposes key points as plain strings, so the traces
are not served - but they are kept in agent_runs.raw_response, and the
evaluator uses them to measure how many key points are actually grounded
in the document. A summary that reads well but invents a renewal term is
worse than no summary, and a reader cannot tell the difference. The check
can.

Model access note
-----------------
Nova must be invoked through an inference profile ("us.amazon.nova-pro-v1:0").
A plain foundation-model ARN fails with ValidationException. Bedrock exceptions
are logged explicitly rather than swallowed.
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

# --- Environment ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]

# Inference profile id, not a foundation-model ARN. See module docstring.
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "summary-v1")

MAX_CONTRACT_CHARS = int(os.environ.get("MAX_CONTRACT_CHARS", "120000"))

# Length targets. "Concise overview" in the SOW; a page of prose is not
# concise, and three sentences cannot cover term, money and termination.
SUMMARY_MIN_WORDS = 120
SUMMARY_MAX_WORDS = 260
KEY_POINTS_MIN = 5
KEY_POINTS_MAX = 10

# Sentences that only list topics - see remove_topic_list_sentences().
TOPIC_LIST_OPENERS = re.compile(
    r"^(?:(?:key|other|important|additional) (?:terms|provisions|clauses) (?:include|cover|address)"
    r"|the agreement (?:also )?(?:includes|contains|covers|has) "
    r"(?:(?:the following|standard|general|usual|customary) )?"
    r"(?:provisions?|terms|clauses|sections)(?: (?:for|on|about|regarding))?)",
    re.IGNORECASE,
)
TOPIC_WORDS = ("confidential", "liabilit", "indemnif", "ownership", "intellectual property",
               "governing law", "dispute", "termination", "warrant", "survival")


def remove_topic_list_sentences(text):
    """
    Drop sentences that name topics without saying anything about them.

    The prompt asks for what the document says, not which topics it has, but
    the model still tends to emit one sentence such as "Key terms include
    ownership of work, confidentiality and limitations on liability" or "The
    agreement includes provisions for confidentiality, limitation of liability
    and indemnification". A reader can do nothing with it, so it goes. A
    sentence is only removed when it opens like a topic list, names at least
    two topics, and carries no number - a sentence with a figure in it is
    saying something.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for sentence in sentences:
        s = sentence.strip()
        low = s.lower()
        is_list = (
            TOPIC_LIST_OPENERS.match(s)
            and sum(1 for w in TOPIC_WORDS if w in low) >= 2
            and not re.search(r"\d", s)
        )
        if is_list:
            print(f"Dropping topic-list sentence: {s[:90]!r}")
            continue
        kept.append(s)
    return " ".join(kept).strip()


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
            (:contract_id::uuid, :extraction_id::uuid, 'summary', 'running',
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


def insert_summary(run_id, contract_id, summary):
    execute_sql(
        """
        INSERT INTO judy_ai.contract_summaries
            (run_id, contract_id, summary_text, key_points, word_count)
        VALUES
            (:run_id::uuid, :contract_id::uuid, :summary_text, :key_points::jsonb, :word_count)
        """,
        [
            string_param("run_id", run_id),
            string_param("contract_id", contract_id),
            string_param("summary_text", summary["summary_text"]),
            string_param("key_points", json.dumps([kp["point"] for kp in summary["key_points"]])),
            long_param("word_count", summary["word_count"]),
        ],
    )


# =============================================================
# Bedrock
# =============================================================
def invoke_model(system_prompt, user_prompt):
    """One Converse call. Bedrock errors are printed before re-raising."""
    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 3000, "temperature": 0},
        )
    except ClientError as e:
        print(f"BEDROCK ERROR modelId={MODEL_ID}: {e}")
        raise

    text = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )
    return text, response.get("usage", {})


def parse_object(text):
    """Pull the JSON object out of a model response, tolerating fences and prose."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()

    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1:
            print(f"No JSON object in model response: {text[:400]}")
            return None
        candidate = candidate[start:end + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"Could not parse model response as JSON ({e}): {candidate[:400]}")
        return None
    return parsed if isinstance(parsed, dict) else None


# =============================================================
# Prompt
# =============================================================
def summary_prompt(contract_text):
    system = (
        "You explain contracts to people who are not lawyers. You restate what a document "
        "says, in plain language, and nothing more. You do not give advice, you do not judge "
        "whether terms are good or bad, and you never state anything the document does not say."
    )

    user = f"""Write a plain-language overview of the contract below for a business reader who has
not read it and is not a lawyer.

The overview is {SUMMARY_MIN_WORDS} to {SUMMARY_MAX_WORDS} words of connected prose, in this order:
1. Who the parties are and what the agreement is for.
2. How long it lasts and how it renews or ends.
3. What is being paid, by whom, when.
4. What each side must do, and the main things each side may not do.
5. The handful of terms a reader would want to know before signing: ownership of work,
   confidentiality, liability limits, anything unusual for a document of this type.

Then give {KEY_POINTS_MIN} to {KEY_POINTS_MAX} key points. Each key point is ONE plain sentence a
reader could act on, and each MUST be tied to the exact sentence in the contract it comes
from. If you cannot quote a sentence for a point, do not make the point.

Rules:
- Plain words. "Ends" not "terminates"; "must" not "shall"; "pay" not "remit"; "must cover
  the other side's losses" not "indemnify". Keep defined terms only where a plain word would
  be wrong.
- Restate; do not copy. Never paste a sentence from the contract into the overview. Say what
  it means in your own words, in sentences of at most 30 words.
- Say what the document says, not which topics it has. A sentence like "key terms include
  ownership, confidentiality and liability" tells the reader nothing. Instead: who owns what,
  what must be kept confidential, what the liability cap is.
- Every fact, number, date and period must appear in the document. Do not round, infer
  or fill gaps. If the document leaves something blank ("$______", "[Company]"), say it is
  left blank.
- No advice, no opinions, no recommendations, no "you should".
- Do not mention this instruction, the format, or that you are summarising.

Return ONLY a JSON object:
{{
  "summary": "the overview, as one or more paragraphs in a single string",
  "key_points": [
    {{
      "point": "one plain sentence",
      "source_quote": "the exact sentence from the contract this comes from, copied verbatim",
      "source_page": <page number from the nearest [page N] marker>
    }}
  ]
}}
No prose outside the JSON object.

=== CONTRACT ===
{contract_text}"""

    return system, user


# =============================================================
# Normalisation
# =============================================================
def word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def normalise(parsed):
    """
    Validate the model's response into the row to store.

    Returns None if there is no usable summary. Key points without a point
    are dropped; key points without a quote are kept but flagged, so the
    evaluator and the stored raw response show exactly which claims are
    unsupported.
    """
    if not isinstance(parsed, dict):
        return None

    summary_text = str(parsed.get("summary") or "").strip()
    if not summary_text:
        print("Model returned no summary text")
        return None

    # The prompt asks for what the document says, not which topics it has, but
    # the model still tends to emit one topic-list sentence ("Key terms include
    # ownership of work, confidentiality and limitations on liability.") before
    # the sentences that actually say those things. It carries no information a
    # reader can act on, so it is removed rather than left for the UI to show.
    summary_text = remove_topic_list_sentences(summary_text)

    key_points = []
    for item in parsed.get("key_points") or []:
        if isinstance(item, str):
            item = {"point": item}
        if not isinstance(item, dict):
            continue
        point = str(item.get("point") or "").strip()
        if not point:
            continue
        page = item.get("source_page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        if page is not None and page < 1:
            page = None
        key_points.append({
            "point": point[:400],
            "source_quote": (str(item.get("source_quote")).strip() or None)
                            if item.get("source_quote") else None,
            "source_page": page,
        })

    if len(key_points) > KEY_POINTS_MAX:
        print(f"Model returned {len(key_points)} key points; keeping the first {KEY_POINTS_MAX}")
        key_points = key_points[:KEY_POINTS_MAX]

    words = word_count(summary_text)
    if words < SUMMARY_MIN_WORDS or words > SUMMARY_MAX_WORDS:
        print(f"Summary is {words} words, outside {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS} - kept")

    return {"summary_text": summary_text, "key_points": key_points, "word_count": words}


# =============================================================
# Handler
# =============================================================
def analyse_text(contract_text):
    """
    Run the model and normalise. Pure - no database access.

    Returns (summary, raw_text, usage). Shared with the local evaluator so
    the code that runs in Lambda is exactly the code that gets measured.
    """
    if len(contract_text) > MAX_CONTRACT_CHARS:
        print(f"Contract text {len(contract_text)} chars, truncating to {MAX_CONTRACT_CHARS}")
        contract_text = contract_text[:MAX_CONTRACT_CHARS]

    system, user = summary_prompt(contract_text)
    raw, usage = invoke_model(system, user)
    summary = normalise(parse_object(raw))

    # One bounded retry when the overview misses the length target. The model
    # is shown its own output and asked to fix only the length, keeping the
    # key points, so the cost is one extra short call and never a loop.
    if summary and not (SUMMARY_MIN_WORDS <= summary["word_count"] <= SUMMARY_MAX_WORDS):
        direction = "shorter" if summary["word_count"] > SUMMARY_MAX_WORDS else "longer"
        print(f"Overview is {summary['word_count']} words; asking once for a {direction} one")
        retry_user = (
            f"{user}\n\n=== YOUR PREVIOUS ANSWER ===\n{raw}\n\n"
            f"Your overview was {summary['word_count']} words. It must be between "
            f"{SUMMARY_MIN_WORDS} and {SUMMARY_MAX_WORDS} words. Rewrite ONLY the overview to be "
            f"{direction}, restating in plain words rather than copying contract sentences, and "
            f"return the same JSON object with the key points unchanged."
        )
        raw2, usage2 = invoke_model(system, retry_user)
        retry = normalise(parse_object(raw2))
        for key in ("inputTokens", "outputTokens"):
            usage[key] = usage.get(key, 0) + usage2.get(key, 0)
        if retry and abs(retry["word_count"] - (SUMMARY_MIN_WORDS + SUMMARY_MAX_WORDS) / 2) \
                < abs(summary["word_count"] - (SUMMARY_MIN_WORDS + SUMMARY_MAX_WORDS) / 2):
            summary, raw = retry, raw2

    if summary:
        grounded = sum(1 for kp in summary["key_points"] if kp["source_quote"])
        print(f"Summary {summary['word_count']} words, {len(summary['key_points'])} key points "
              f"({grounded} with a source quote)")
    return summary, raw, usage


def analyse(contract_id, extraction_id):
    contract_text = fetch_extraction(extraction_id)
    run_id = start_run(contract_id, extraction_id)
    print(f"Run {run_id} started for contract {contract_id}")

    started = time.time()
    try:
        summary, raw, usage = analyse_text(contract_text)
        if summary is None:
            raise RuntimeError("Model returned no usable summary")

        latency_ms = int((time.time() - started) * 1000)
        insert_summary(run_id, contract_id, summary)
        # raw_response keeps the traced key points (quote + page per point),
        # which the served API does not expose but manual validation needs.
        complete_run(run_id, {"model_output": raw, "key_points_traced": summary["key_points"]},
                     usage, latency_ms)

        print(f"Run {run_id} succeeded: {summary['word_count']} words, "
              f"{len(summary['key_points'])} key points in {latency_ms} ms")
        return {"run_id": run_id, "word_count": summary["word_count"],
                "key_points": len(summary["key_points"])}

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
