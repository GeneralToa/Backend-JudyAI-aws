"""
ingestion_post_processor Lambda function.

Triggered by a dynamic EventBridge Scheduler schedule (rate: 30 seconds).
Receives job_id and schedule_name in the event input.

Logic:
1. Get the specific ingestion job status by job_id
2. If COMPLETE: update tagged docs, delete 'deleting' items, delete schedule
3. If FAILED: mark tagged docs as 'failed', delete schedule
4. If IN_PROGRESS: do nothing (wait for next trigger in 30s)
5. After processing: check for orphaned documents and start follow-up if needed
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

# --- Clients ---
ssm_client = boto3.client("ssm")
bedrock_agent_client = boto3.client("bedrock-agent")
dynamodb_client = boto3.client("dynamodb")
scheduler_client = boto3.client("scheduler")

# --- Environment Variables ---
KB_ID_PARAM_NAME = os.environ["KB_ID_PARAM_NAME"]
DS_ID_PARAM_NAME = os.environ["DS_ID_PARAM_NAME"]
DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
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


def lambda_handler(event, context):
    """Check specific ingestion job status and update DynamoDB accordingly."""
    print(f"ingestion_post_processor invoked: {json.dumps(event)}")

    job_id = event.get("job_id")
    schedule_name = event.get("schedule_name")

    if not job_id or not schedule_name:
        print("Missing job_id or schedule_name in event.")
        return {"statusCode": 400, "body": "missing job_id or schedule_name"}

    knowledge_base_id, data_source_id = get_kb_params()
    job_status, job_stats = get_job_status(knowledge_base_id, data_source_id, job_id)

    if job_status is None:
        print(f"Could not retrieve status for job {job_id}")
        return {"statusCode": 200, "body": "job not found"}

    print(f"Ingestion job {job_id} status: {job_status}")

    if job_status in ("STARTING", "IN_PROGRESS"):
        print("Job still in progress. Will check again in 30 seconds.")
        return {"statusCode": 200, "body": "in progress"}

    # Terminal state reached - process results
    if job_status == "COMPLETE":
        handle_job_complete(job_id, job_stats)
    elif job_status == "FAILED":
        handle_job_failed(job_id)

    # Delete the schedule that triggered us
    delete_schedule(schedule_name)

    # Check for orphaned documents and start follow-up job if needed
    handle_orphaned_documents(knowledge_base_id, data_source_id)

    return {"statusCode": 200, "body": f"processed: {job_status}"}


# =============================================================
# Job Result Handlers
# =============================================================
def handle_job_complete(job_id, job_stats):
    """Handle completed job. Update loading docs, delete deleting docs."""
    docs_failed = job_stats.get("numberOfDocumentsFailed", 0)

    loading_docs = get_documents_by_status_and_job("loading", job_id)
    if loading_docs:
        if docs_failed > 0 and job_stats.get("numberOfNewDocumentsIndexed", 0) == 0:
            update_documents_status(loading_docs, "failed")
            print(f"Updated {len(loading_docs)} loading docs to 'failed' for job {job_id}")
        else:
            update_documents_status(loading_docs, "ingested")
            print(f"Updated {len(loading_docs)} loading docs to 'ingested' for job {job_id}")

    deleting_docs = get_documents_by_status_and_job("deleting", job_id)
    if deleting_docs:
        for doc in deleting_docs:
            document_id = doc["document_id"]["S"]
            try:
                dynamodb_client.delete_item(
                    TableName=DOCUMENTS_TABLE,
                    Key={"document_id": {"S": document_id}},
                )
                print(f"Deleted DynamoDB item: {document_id}")
            except Exception as e:
                print(f"Error deleting item {document_id}: {e}")


def handle_job_failed(job_id):
    """Handle failed job. Mark all tagged docs as 'failed'."""
    loading_docs = get_documents_by_status_and_job("loading", job_id)
    if loading_docs:
        update_documents_status(loading_docs, "failed")
        print(f"Updated {len(loading_docs)} loading docs to 'failed' for job {job_id}")

    deleting_docs = get_documents_by_status_and_job("deleting", job_id)
    if deleting_docs:
        update_documents_status(deleting_docs, "failed")
        print(f"Updated {len(deleting_docs)} deleting docs to 'failed' for job {job_id}")


# =============================================================
# Follow-up: Handle Orphaned Documents
# =============================================================
def handle_orphaned_documents(knowledge_base_id, data_source_id):
    """
    Check for orphaned documents (loading/deleting without ingestion_job_id).
    If found, start a new ingestion job, tag them, and create a new schedule.
    """
    orphaned_loading = get_orphaned_documents("loading")
    orphaned_deleting = get_orphaned_documents("deleting")
    total_orphans = len(orphaned_loading) + len(orphaned_deleting)

    if total_orphans == 0:
        print("No orphaned documents. Chain complete.")
        return

    print(f"Found {total_orphans} orphaned documents. Starting follow-up job.")

    try:
        resp = bedrock_agent_client.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
        )
        new_job_id = resp["ingestionJob"]["ingestionJobId"]
        print(f"Follow-up ingestion job started: {new_job_id}")

        # Tag orphans with the new job ID
        tag_documents_with_job(orphaned_loading, new_job_id)
        tag_documents_with_job(orphaned_deleting, new_job_id)
        print(f"Tagged {total_orphans} orphans with job_id: {new_job_id}")

        # Create a new schedule to monitor the follow-up job
        create_monitor_schedule(new_job_id)

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ConflictException":
            print("ConflictException on follow-up. Orphans will be retried later.")
        else:
            print(f"Error starting follow-up job: {e}")


def create_monitor_schedule(job_id):
    """Create an EventBridge Scheduler schedule to monitor a job."""
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
    print(f"Created follow-up schedule: {schedule_name}")


# =============================================================
# Utility Functions
# =============================================================
def get_job_status(knowledge_base_id, data_source_id, job_id):
    """Get status and statistics of a specific ingestion job."""
    try:
        resp = bedrock_agent_client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        job = resp.get("ingestionJob", {})
        return job.get("status"), job.get("statistics", {})
    except Exception as e:
        print(f"Error getting job {job_id}: {e}")
        return None, {}


def get_documents_by_status_and_job(status, job_id):
    """Get documents matching kb_status AND ingestion_job_id."""
    items = []
    scan_kwargs = {
        "TableName": DOCUMENTS_TABLE,
        "FilterExpression": "kb_status = :status AND ingestion_job_id = :job_id",
        "ExpressionAttributeValues": {
            ":status": {"S": status},
            ":job_id": {"S": job_id},
        },
    }
    while True:
        result = dynamodb_client.scan(**scan_kwargs)
        items.extend(result.get("Items", []))
        if "LastEvaluatedKey" in result:
            scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
        else:
            break
    return items


def get_orphaned_documents(status):
    """Get documents with given status that have NO ingestion_job_id."""
    items = []
    scan_kwargs = {
        "TableName": DOCUMENTS_TABLE,
        "FilterExpression": "kb_status = :status AND attribute_not_exists(ingestion_job_id)",
        "ExpressionAttributeValues": {":status": {"S": status}},
    }
    while True:
        result = dynamodb_client.scan(**scan_kwargs)
        items.extend(result.get("Items", []))
        if "LastEvaluatedKey" in result:
            scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
        else:
            break
    return items


def tag_documents_with_job(documents, job_id):
    """Tag documents with an ingestion_job_id."""
    for doc in documents:
        document_id = doc["document_id"]["S"]
        try:
            dynamodb_client.update_item(
                TableName=DOCUMENTS_TABLE,
                Key={"document_id": {"S": document_id}},
                UpdateExpression="SET ingestion_job_id = :job_id",
                ExpressionAttributeValues={":job_id": {"S": job_id}},
            )
        except Exception as e:
            print(f"Error tagging {document_id}: {e}")


def update_documents_status(documents, new_status):
    """Update kb_status and remove ingestion_job_id."""
    for doc in documents:
        document_id = doc["document_id"]["S"]
        try:
            dynamodb_client.update_item(
                TableName=DOCUMENTS_TABLE,
                Key={"document_id": {"S": document_id}},
                UpdateExpression="SET kb_status = :status REMOVE ingestion_job_id",
                ExpressionAttributeValues={":status": {"S": new_status}},
            )
        except Exception as e:
            print(f"Error updating {document_id}: {e}")


def delete_schedule(schedule_name):
    """Delete the EventBridge Scheduler schedule (self-cleanup)."""
    try:
        scheduler_client.delete_schedule(Name=schedule_name)
        print(f"Deleted schedule: {schedule_name}")
    except Exception as e:
        print(f"Error deleting schedule {schedule_name}: {e}")
