"""
data_processor Lambda function.

Multi-trigger handler supporting:
1. SQS Event (from S3 notification) → Update file size in DynamoDB, start KB ingestion
2. API Gateway GET /files → List all documents from DynamoDB
3. API Gateway DELETE /files/{document_id} → Delete from S3, set status to 'deleting', re-sync KB

When an ingestion job is started (upload or delete), this Lambda creates a dynamic
EventBridge Scheduler schedule (rate: 30 seconds) that triggers the
ingestion_post_processor Lambda. The post-processor deletes the schedule once
the job completes.

If a ConflictException occurs (job already running), documents are left as
"orphans" (no ingestion_job_id). The post-processor detects these after the
current job completes and starts a follow-up job.
"""

import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# --- Clients ---
ssm_client = boto3.client("ssm")
bedrock_agent_client = boto3.client("bedrock-agent")
dynamodb_client = boto3.client("dynamodb")
s3_client = boto3.client("s3")
scheduler_client = boto3.client("scheduler")

# --- Environment Variables ---
KB_ID_PARAM_NAME = os.environ["KB_ID_PARAM_NAME"]
DS_ID_PARAM_NAME = os.environ["DS_ID_PARAM_NAME"]
DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
LANDING_ZONE_BUCKET = os.environ["LANDING_ZONE_BUCKET"]
INGESTION_POST_PROCESSOR_ARN = os.environ["INGESTION_POST_PROCESSOR_ARN"]
RESOURCE_PREFIX = os.environ["RESOURCE_PREFIX"]
SCHEDULER_ROLE_ARN = os.environ["SCHEDULER_ROLE_ARN"]

# --- Cached SSM parameters ---
_kb_id = None
_ds_id = None


def get_kb_params():
    """Get Knowledge Base and Data Source IDs (cached across warm invocations)."""
    global _kb_id, _ds_id
    if _kb_id is None:
        _kb_id = ssm_client.get_parameter(Name=KB_ID_PARAM_NAME)["Parameter"]["Value"]
        _ds_id = ssm_client.get_parameter(Name=DS_ID_PARAM_NAME)["Parameter"]["Value"]
    return _kb_id, _ds_id


def response(status_code, body):
    """Build API Gateway response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,GET,DELETE",
        },
        "body": json.dumps(body),
    }


# =============================================================
# EventBridge Scheduler Management
# =============================================================
def create_ingestion_monitor_schedule(job_id):
    """Create a dynamic EventBridge Scheduler schedule (rate: 30 seconds)."""
    schedule_name = f"{RESOURCE_PREFIX}-monitor-{job_id}"

    scheduler_client.create_schedule(
        Name=schedule_name,
        ScheduleExpression="rate(1 minute)",
        FlexibleTimeWindow={"Mode": "OFF"},
        State="ENABLED",
        Description=f"Monitors ingestion job {job_id} status",
        Target={
            "Arn": INGESTION_POST_PROCESSOR_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps({
                "job_id": job_id,
                "schedule_name": schedule_name,
            }),
        },
    )

    print(f"Created EventBridge schedule: {schedule_name} (rate: 1 minute)")
    return schedule_name


def start_ingestion_and_monitor(knowledge_base_id, data_source_id):
    """
    Attempt to start an ingestion job. If successful, create a monitor schedule.
    Returns (job_id, started). If ConflictException, returns (None, False).
    """
    try:
        resp = bedrock_agent_client.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        print(f"Ingestion job started: {job_id}")

        create_ingestion_monitor_schedule(job_id)
        return job_id, True

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ConflictException":
            print("ConflictException: ingestion job already running. "
                  "Documents left as orphans for follow-up processing.")
            return None, False
        else:
            raise


# =============================================================
# Event Router
# =============================================================
def lambda_handler(event, context):
    """Route event to appropriate handler based on source."""
    print(f"Event received: {json.dumps(event)[:500]}")

    # Support both API Gateway REST (v1: httpMethod) and HTTP API (v2: routeKey)
    if "httpMethod" in event or "routeKey" in event:
        return handle_api_gateway(event)

    if "Records" in event:
        first_record = event["Records"][0]
        if first_record.get("eventSource") == "aws:sqs":
            return handle_sqs_trigger(event)

    print(f"Unknown event source, ignoring: {json.dumps(event)[:200]}")
    return {"statusCode": 200, "body": "unknown event - ignored"}


# =============================================================
# Handler: SQS Trigger → Update DynamoDB + Start Ingestion
# =============================================================
def handle_sqs_trigger(event):
    """
    Triggered by SQS event source mapping (batchSize=1).
    1. Parse S3 event to update file size in DynamoDB
    2. Try to start ingestion job
    3. If successful: tag orphaned 'loading' docs with job_id
    4. If ConflictException: docs remain orphans, post-processor will handle
    """
    for record in event["Records"]:
        body = json.loads(record["body"])
        s3_records = body.get("Records", [])
        for s3_record in s3_records:
            store_document_metadata(s3_record)

    knowledge_base_id, data_source_id = get_kb_params()

    if has_running_job(knowledge_base_id, data_source_id):
        print("Ingestion job already running. Docs left as orphans for follow-up.")
        return {"statusCode": 200, "body": "metadata stored, ingestion deferred"}

    job_id, started = start_ingestion_and_monitor(knowledge_base_id, data_source_id)

    if started:
        tag_orphaned_documents_with_job("loading", job_id)
        return {"statusCode": 200, "body": f"metadata stored, ingestion started: {job_id}"}
    else:
        return {"statusCode": 200, "body": "metadata stored, ingestion deferred (conflict)"}


def tag_orphaned_documents_with_job(status, job_id):
    """Associate all documents with given kb_status and no ingestion_job_id to this job."""
    scan_kwargs = {
        "TableName": DOCUMENTS_TABLE,
        "FilterExpression": "kb_status = :status AND attribute_not_exists(ingestion_job_id)",
        "ExpressionAttributeValues": {":status": {"S": status}},
    }

    tagged_count = 0
    while True:
        result = dynamodb_client.scan(**scan_kwargs)
        for item in result.get("Items", []):
            document_id = item["document_id"]["S"]
            try:
                dynamodb_client.update_item(
                    TableName=DOCUMENTS_TABLE,
                    Key={"document_id": {"S": document_id}},
                    UpdateExpression="SET ingestion_job_id = :job_id",
                    ExpressionAttributeValues={":job_id": {"S": job_id}},
                )
                tagged_count += 1
            except Exception as e:
                print(f"Error tagging document {document_id}: {e}")

        if "LastEvaluatedKey" in result:
            scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
        else:
            break

    print(f"Tagged {tagged_count} orphaned '{status}' documents with job_id: {job_id}")


def store_document_metadata(s3_record):
    """Extract file info from S3 event record and update DynamoDB with file size."""
    try:
        s3_info = s3_record.get("s3", {})
        key = s3_info.get("object", {}).get("key", "")
        size = s3_info.get("object", {}).get("size", 0)

        from urllib.parse import unquote_plus
        key = unquote_plus(key)

        if not key.startswith("uploads/"):
            return

        parts = key.split("/")
        if len(parts) < 3:
            return

        document_id = parts[1]
        file_size_kb = round(size / 1024, 2)

        dynamodb_client.update_item(
            TableName=DOCUMENTS_TABLE,
            Key={"document_id": {"S": document_id}},
            UpdateExpression="SET file_size_kb = :size",
            ExpressionAttributeValues={":size": {"N": str(file_size_kb)}},
        )
        print(f"Document metadata updated: {document_id} ({file_size_kb} KB)")

    except Exception as e:
        print(f"Error updating document metadata: {e}")


def has_running_job(knowledge_base_id, data_source_id):
    """Check if there is an active ingestion job."""
    for status in ["STARTING", "IN_PROGRESS"]:
        resp = bedrock_agent_client.list_ingestion_jobs(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            filters=[{"attribute": "STATUS", "operator": "EQ", "values": [status]}],
            maxResults=1,
        )
        if resp.get("ingestionJobSummaries"):
            return True
    return False


# =============================================================
# Handler: API Gateway
# =============================================================
def handle_api_gateway(event):
    # Support both REST API (v1) and HTTP API (v2) event formats
    if "routeKey" in event:
        # v2: routeKey is like "GET /files" or "DELETE /files/{documentId}"
        route_key = event.get("routeKey", "")
        parts = route_key.split(" ", 1)
        http_method = parts[0] if parts else ""
        resource = parts[1] if len(parts) > 1 else ""
    else:
        # v1: httpMethod and resource are separate fields
        http_method = event.get("httpMethod", "")
        resource = event.get("resource", "")

    if http_method == "GET" and resource == "/files":
        return handle_list_files(event)
    elif http_method == "DELETE" and "/files/" in resource:
        return handle_delete_file(event)
    else:
        return response(400, {"error": f"Unsupported: {http_method} {resource}"})


def handle_list_files(event):
    """GET /files → Scan DynamoDB and return all documents."""
    try:
        items = []
        scan_kwargs = {"TableName": DOCUMENTS_TABLE}

        while True:
            result = dynamodb_client.scan(**scan_kwargs)
            items.extend(result.get("Items", []))
            if "LastEvaluatedKey" in result:
                scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
            else:
                break

        files = []
        for item in items:
            # Skip incomplete records (e.g., created by data_processor UpdateItem
            # without a prior data_upload PutItem)
            if "document_name" not in item:
                continue
            files.append({
                "document_id": item["document_id"]["S"],
                "document_name": item["document_name"]["S"],
                "upload_date": item.get("upload_date", {}).get("S", ""),
                "file_size_kb": float(item.get("file_size_kb", {}).get("N", "0")),
                "uploaded_by": item.get("uploaded_by", {}).get("S", "unknown"),
                "kb_status": item.get("kb_status", {}).get("S", "loading"),
            })

        files.sort(key=lambda f: f["upload_date"], reverse=True)
        return response(200, {"files": files})

    except Exception as e:
        print(f"Error listing files: {e}")
        return response(500, {"error": "Failed to list files"})


def handle_delete_file(event):
    """
    DELETE /files/{document_id}
    1. Get document, delete from S3
    2. Set kb_status='deleting' (orphan - no job_id yet)
    3. Try to start re-sync; if conflict, post-processor will handle later
    """
    path_params = event.get("pathParameters") or {}
    document_id = path_params.get("document_id") or path_params.get("documentId", "")

    try:
        result = dynamodb_client.get_item(
            TableName=DOCUMENTS_TABLE,
            Key={"document_id": {"S": document_id}},
        )

        if "Item" not in result:
            return response(404, {"error": "Document not found"})

        item = result["Item"]
        s3_key = item["s3_key"]["S"]
        document_name = item["document_name"]["S"]

        # Delete from S3
        s3_client.delete_object(Bucket=LANDING_ZONE_BUCKET, Key=s3_key)
        print(f"Deleted from S3: {s3_key}")

        # Set kb_status to 'deleting' (orphan)
        dynamodb_client.update_item(
            TableName=DOCUMENTS_TABLE,
            Key={"document_id": {"S": document_id}},
            UpdateExpression="SET kb_status = :status REMOVE ingestion_job_id",
            ExpressionAttributeValues={":status": {"S": "deleting"}},
        )
        print(f"Set kb_status to 'deleting' for: {document_id}")

        # Try to start KB re-sync
        knowledge_base_id, data_source_id = get_kb_params()
        job_id, started = start_ingestion_and_monitor(knowledge_base_id, data_source_id)

        if started:
            tag_orphaned_documents_with_job("deleting", job_id)
            tag_orphaned_documents_with_job("loading", job_id)

            return response(200, {
                "message": "Document deletion in progress",
                "document_id": document_id,
                "document_name": document_name,
            })
        else:
            return response(200, {
                "message": "Document marked for deletion (sync pending)",
                "document_id": document_id,
                "document_name": document_name,
            })

    except Exception as e:
        print(f"Error deleting file: {e}")
        return response(500, {"error": "Failed to delete document"})
