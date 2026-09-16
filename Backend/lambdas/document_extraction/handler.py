"""
document_extraction Lambda function.

First stage of the AI pipeline. Every one of the four SOW agents consumes the
text this function produces, so nothing downstream runs until this succeeds.

Trigger:
    SQS event (from the S3 upload notification on the landing zone bucket),
    matching the pattern already used by data_processor.

Flow:
    1. Resolve the uploaded S3 object to a row in app.contracts
    2. Insert a judy_ai.document_extractions row (status 'running') immediately,
       recording the BDA invocation ARN before we wait on anything
    3. Invoke Bedrock Data Automation asynchronously
    4. Wait for BDA to finish, bounded by MAX_WAIT_SECONDS
    5. Read the BDA output from S3, normalize it to plain text
    6. Store the text and mark the extraction 'succeeded'
    7. Fire the three pre-signing agents asynchronously

The SOW mandates Bedrock Data Automation for text extraction:
    "The team will use bedrock data automation to extract text from PDF and
     Word formats. Document parsing failures are put into a dead letter queue
     for manual resolution by customer."

Why we wait inline instead of polling on a schedule:
    BDA is asynchronous. data_processor solves the equivalent problem for KB
    ingestion with an EventBridge Scheduler plus a second Lambda. That machinery
    is worth it for jobs that run for many minutes; for a document capped by the
    SOW at 20 pages / 10 MB, BDA normally finishes well inside a single
    invocation, so a bounded inline wait keeps this to one function and one IAM
    role.

    This is safe to time out. The extraction row is written with its invocation
    ARN *before* the wait starts, so a slow job leaves a 'running' row that can
    be reconciled later rather than being lost. If BDA turns out to be slow in
    practice, switch step 4 to a BDA EventBridge completion notification without
    touching anything else in this file.
"""

import json
import os
import time
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

# --- Clients ---
bda_runtime_client = boto3.client("bedrock-data-automation-runtime")
s3_client = boto3.client("s3")
rds_data_client = boto3.client("rds-data")
lambda_client = boto3.client("lambda")

# --- Environment Variables ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]
BDA_PROJECT_ARN = os.environ["BDA_PROJECT_ARN"]
BDA_PROFILE_ARN = os.environ["BDA_PROFILE_ARN"]
BDA_OUTPUT_BUCKET = os.environ["BDA_OUTPUT_BUCKET"]
BDA_OUTPUT_PREFIX = os.environ.get("BDA_OUTPUT_PREFIX", "bda-output")

# Comma-separated ARNs of the three pre-signing agent functions. Optional so
# this Lambda can be deployed and exercised before the agents exist.
PRE_SIGNING_AGENT_ARNS = [
    arn.strip()
    for arn in os.environ.get("PRE_SIGNING_AGENT_ARNS", "").split(",")
    if arn.strip()
]

MAX_WAIT_SECONDS = int(os.environ.get("MAX_WAIT_SECONDS", "240"))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))


# =============================================================
# Aurora helpers (RDS Data API)
# =============================================================
# The Data API is used rather than a direct Postgres connection so this Lambda
# does not need VPC configuration. Every statement is parameterized - S3 keys
# and file names originate from user uploads and must never be interpolated
# into SQL.
def execute_sql(sql, parameters=None):
    """Run a parameterized statement against Aurora and return its records."""
    result = rds_data_client.execute_statement(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=AURORA_SECRET_ARN,
        database=AURORA_DATABASE,
        sql=sql,
        parameters=parameters or [],
    )
    return result.get("records", [])


def string_param(name, value):
    """Build a Data API string parameter, mapping None to SQL NULL."""
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}}


def long_param(name, value):
    """Build a Data API integer parameter, mapping None to SQL NULL."""
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"longValue": int(value)}}


def resolve_contract_id(bucket, key):
    """
    Find the app.contracts row for an uploaded object.

    The application creates this row at upload time. If it is missing we do not
    invent one - contracts is app-owned, and silently creating rows here would
    hide an upload bug behind a successful-looking extraction.
    """
    records = execute_sql(
        "SELECT id::text FROM app.contracts WHERE s3_bucket = :bucket AND s3_key = :key",
        [string_param("bucket", bucket), string_param("key", key)],
    )
    if not records:
        return None
    return records[0][0]["stringValue"]


def create_extraction(contract_id, invocation_arn):
    """Insert a 'running' extraction row and return its id."""
    records = execute_sql(
        """
        INSERT INTO judy_ai.document_extractions
            (contract_id, bda_invocation_arn, status)
        VALUES
            (:contract_id::uuid, :invocation_arn, 'running')
        RETURNING id::text
        """,
        [
            string_param("contract_id", contract_id),
            string_param("invocation_arn", invocation_arn),
        ],
    )
    return records[0][0]["stringValue"]


def complete_extraction(extraction_id, text, page_count, output_s3_key):
    """Mark an extraction succeeded and store the normalized text."""
    execute_sql(
        """
        UPDATE judy_ai.document_extractions
           SET status            = 'succeeded',
               extracted_text    = :text,
               page_count        = :page_count,
               char_count        = :char_count,
               raw_output_s3_key = :raw_key,
               completed_at      = now()
         WHERE id = :id::uuid
        """,
        [
            string_param("id", extraction_id),
            string_param("text", text),
            long_param("page_count", page_count),
            long_param("char_count", len(text)),
            string_param("raw_key", output_s3_key),
        ],
    )


def fail_extraction(extraction_id, error_message):
    """Mark an extraction failed. Truncated to keep one bad document from bloating the row."""
    execute_sql(
        """
        UPDATE judy_ai.document_extractions
           SET status        = 'failed',
               error_message = :error,
               completed_at  = now()
         WHERE id = :id::uuid
        """,
        [
            string_param("id", extraction_id),
            string_param("error", error_message[:2000]),
        ],
    )


# =============================================================
# Bedrock Data Automation
# =============================================================
def invoke_bda(bucket, key):
    """Start an asynchronous BDA job and return its invocation ARN."""
    output_prefix = f"s3://{BDA_OUTPUT_BUCKET}/{BDA_OUTPUT_PREFIX}"

    response = bda_runtime_client.invoke_data_automation_async(
        inputConfiguration={"s3Uri": f"s3://{bucket}/{key}"},
        outputConfiguration={"s3Uri": output_prefix},
        dataAutomationConfiguration={
            "dataAutomationProjectArn": BDA_PROJECT_ARN,
            "stage": "LIVE",
        },
        dataAutomationProfileArn=BDA_PROFILE_ARN,
    )

    invocation_arn = response["invocationArn"]
    print(f"BDA invocation started: {invocation_arn} for s3://{bucket}/{key}")
    return invocation_arn


def wait_for_bda(invocation_arn):
    """
    Poll BDA until it reaches a terminal state or MAX_WAIT_SECONDS elapses.

    Returns (status, output_s3_uri, error_message). A timeout returns status
    'InProgress' with no output, which the caller treats as "leave the row
    running" rather than as a failure.
    """
    deadline = time.time() + MAX_WAIT_SECONDS

    while time.time() < deadline:
        status_response = bda_runtime_client.get_data_automation_status(
            invocationArn=invocation_arn
        )
        status = status_response["status"]

        if status == "Success":
            output_uri = status_response.get("outputConfiguration", {}).get("s3Uri")
            print(f"BDA succeeded: {invocation_arn} -> {output_uri}")
            return status, output_uri, None

        if status in ("ServiceError", "ClientError"):
            error_message = status_response.get("errorMessage", status)
            print(f"BDA failed ({status}): {error_message}")
            return status, None, error_message

        time.sleep(POLL_INTERVAL_SECONDS)

    print(f"BDA still running after {MAX_WAIT_SECONDS}s: {invocation_arn}")
    return "InProgress", None, None


def read_s3_json(s3_uri):
    """Read and parse a JSON object identified by an s3:// URI."""
    without_scheme = s3_uri.replace("s3://", "", 1)
    bucket, _, key = without_scheme.partition("/")
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body), bucket, key


def collect_text(node, collected):
    """
    Walk BDA output and collect page text in document order.

    BDA standard output nests page content differently depending on the input
    format, so rather than hard-coding one path this walks the structure and
    picks up the text-bearing fields wherever they appear. Markdown is preferred
    over plain text because it preserves the table structure that contract
    clauses frequently rely on.
    """
    if isinstance(node, dict):
        representation = node.get("representation")
        if isinstance(representation, dict):
            value = representation.get("markdown") or representation.get("text")
            if isinstance(value, str) and value.strip():
                collected.append(value.strip())
                return

        for key in ("markdown", "text"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                collected.append(value.strip())
                return

        for value in node.values():
            collect_text(value, collected)

    elif isinstance(node, list):
        for item in node:
            collect_text(item, collected)


def extract_text_from_bda_output(output_s3_uri):
    """
    Resolve BDA's job metadata to the standard output document and normalize it.

    BDA writes a job_metadata.json describing one or more output segments; the
    actual content lives in the standard output file each segment points at.

    Returns (text, page_count, output_s3_key).
    """
    metadata, _, metadata_key = read_s3_json(output_s3_uri)

    segments = metadata.get("output_metadata") or metadata.get("outputMetadata") or []

    standard_output_uris = []
    for segment in segments:
        for asset in segment.get("segment_metadata", segment.get("segmentMetadata", [])):
            uri = (
                asset.get("standard_output_path")
                or asset.get("standardOutputPath")
                or asset.get("standard_output_uri")
            )
            if uri:
                standard_output_uris.append(uri)

    # Fall back to the metadata document itself if it already carries content,
    # which happens for single-segment results.
    if not standard_output_uris:
        collected = []
        collect_text(metadata, collected)
        text = "\n\n".join(collected)
        return text, count_pages(metadata), metadata_key

    collected = []
    page_count = 0
    last_key = metadata_key

    for uri in standard_output_uris:
        document, _, key = read_s3_json(uri)
        last_key = key
        page_count += count_pages(document)
        collect_text(document, collected)

    return "\n\n".join(collected), page_count, last_key


def count_pages(document):
    """Best-effort page count from BDA output."""
    pages = document.get("pages")
    if isinstance(pages, list):
        return len(pages)

    metadata = document.get("metadata") or {}
    for key in ("number_of_pages", "numberOfPages", "page_count", "pageCount"):
        if isinstance(metadata.get(key), int):
            return metadata[key]

    return 0


# =============================================================
# Downstream agents
# =============================================================
def trigger_pre_signing_agents(contract_id, extraction_id):
    """
    Fire the three pre-signing agents asynchronously.

    Invoked with InvocationType='Event' so extraction is not blocked by agent
    runtime, and so one failing agent cannot fail the others or this function.
    """
    if not PRE_SIGNING_AGENT_ARNS:
        print("No pre-signing agent ARNs configured yet - skipping downstream trigger")
        return

    payload = json.dumps({"contract_id": contract_id, "extraction_id": extraction_id})

    for arn in PRE_SIGNING_AGENT_ARNS:
        try:
            lambda_client.invoke(
                FunctionName=arn,
                InvocationType="Event",
                Payload=payload,
            )
            print(f"Triggered agent: {arn}")
        except ClientError as e:
            # Deliberately swallowed: extraction has already succeeded and is
            # persisted. A failed trigger is recoverable by re-running analysis
            # through the API; failing here would discard good extracted text.
            print(f"Failed to trigger agent {arn}: {e}")


# =============================================================
# Pipeline
# =============================================================
def process_document(bucket, key):
    """Run the full extraction pipeline for one uploaded object."""
    print(f"Processing s3://{bucket}/{key}")

    contract_id = resolve_contract_id(bucket, key)
    if contract_id is None:
        # Raising sends the SQS message to the DLQ, which is what the SOW asks
        # for: parsing problems surface for manual resolution rather than being
        # silently dropped.
        raise RuntimeError(
            f"No app.contracts row for s3://{bucket}/{key} - "
            "the application must create the contract record at upload time"
        )

    invocation_arn = invoke_bda(bucket, key)
    extraction_id = create_extraction(contract_id, invocation_arn)
    print(f"Extraction {extraction_id} created for contract {contract_id}")

    status, output_uri, error_message = wait_for_bda(invocation_arn)

    if status == "InProgress":
        # Left deliberately as 'running' with its invocation ARN recorded.
        print(f"Extraction {extraction_id} left running for later reconciliation")
        return

    if status != "Success":
        fail_extraction(extraction_id, error_message or f"BDA returned {status}")
        raise RuntimeError(f"BDA extraction failed for s3://{bucket}/{key}: {error_message}")

    text, page_count, output_key = extract_text_from_bda_output(output_uri)

    if not text.strip():
        fail_extraction(extraction_id, "BDA returned no extractable text")
        raise RuntimeError(f"No extractable text in s3://{bucket}/{key}")

    complete_extraction(extraction_id, text, page_count, output_key)
    print(
        f"Extraction {extraction_id} succeeded: "
        f"{page_count} pages, {len(text)} characters"
    )

    trigger_pre_signing_agents(contract_id, extraction_id)


def lambda_handler(event, context):
    """Entry point. Handles SQS batches carrying S3 upload notifications."""
    print(f"Event received: {json.dumps(event)[:500]}")

    records = event.get("Records", [])
    if not records:
        print("No records in event - ignoring")
        return {"statusCode": 200, "body": "no records"}

    processed = 0

    for record in records:
        body = json.loads(record["body"])

        for s3_record in body.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            # S3 event keys are URL-encoded; file names in this project contain
            # spaces (for example "Mutual NDA_Template.docx").
            key = unquote_plus(s3_record["s3"]["object"]["key"])
            process_document(bucket, key)
            processed += 1

    return {"statusCode": 200, "body": f"processed {processed} document(s)"}
