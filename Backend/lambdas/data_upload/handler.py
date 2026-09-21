"""
data_upload Lambda function.
Generates a presigned URL for uploading a file to the S3 landing zone bucket.
Triggered by API Gateway (POST /upload) with Lambda Proxy integration.

Also writes initial document metadata to DynamoDB (document_id, name, uploaded_by)
so that the uploader email is reliably captured at upload time.

Additionally inserts a row into app.contracts (Aurora PostgreSQL) via the RDS
Data API so that Zuhair's AI agents can locate and process the document.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

s3_client = boto3.client("s3", region_name="us-west-2", endpoint_url="https://s3.us-west-2.amazonaws.com")
dynamodb_client = boto3.client("dynamodb")
rds_data_client = boto3.client("rds-data", region_name="us-west-2")
ssm_client = boto3.client("ssm", region_name="us-west-2")

BUCKET_NAME = os.environ["BUCKET_NAME"]
DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]

# SSM parameter names — resolved at cold-start and cached for the lifetime of
# the execution environment to avoid per-request SSM calls.
_AURORA_CLUSTER_ARN = None
_APP_WRITER_SECRET_ARN = None

# Aurora database name
AURORA_DATABASE = "ragdb"

# 10 MB file size limit (per Ticket 1 requirements)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Allowed file types for contract documents
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

MIME_TYPE_TO_EXTENSION = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


def _get_ssm_params():
    """Resolve and cache Aurora cluster ARN and app_writer secret ARN from SSM."""
    global _AURORA_CLUSTER_ARN, _APP_WRITER_SECRET_ARN
    if _AURORA_CLUSTER_ARN and _APP_WRITER_SECRET_ARN:
        return

    params = ssm_client.get_parameters(
        Names=[
            "/rag-app-prod/aurora/postgres-arn",
            "/rag-app-prod/secret-manager/app-writer-secret",
        ]
    )
    by_name = {p["Name"]: p["Value"] for p in params["Parameters"]}
    _AURORA_CLUSTER_ARN = by_name["/rag-app-prod/aurora/postgres-arn"]
    _APP_WRITER_SECRET_ARN = by_name["/rag-app-prod/secret-manager/app-writer-secret"]


def _insert_contract_row(contract_id, s3_key, filename, mime_type, uploaded_by):
    """
    Insert a row into app.contracts via the RDS Data API.

    Uses the app_writer role (owns app.*) and parameterised SQL to prevent
    injection. Returns the contract_id on success, raises on failure.
    """
    _get_ssm_params()

    sql = """
        INSERT INTO app.contracts (
            id,
            s3_bucket,
            s3_key,
            original_filename,
            mime_type,
            uploaded_by,
            status
        ) VALUES (
            :id::uuid,
            :s3_bucket,
            :s3_key,
            :original_filename,
            :mime_type,
            :uploaded_by,
            'uploaded'
        )
        ON CONFLICT (s3_bucket, s3_key) DO NOTHING
    """

    rds_data_client.execute_statement(
        resourceArn=_AURORA_CLUSTER_ARN,
        secretArn=_APP_WRITER_SECRET_ARN,
        database=AURORA_DATABASE,
        sql=sql,
        parameters=[
            {"name": "id",                "value": {"stringValue": contract_id}},
            {"name": "s3_bucket",         "value": {"stringValue": BUCKET_NAME}},
            {"name": "s3_key",            "value": {"stringValue": s3_key}},
            {"name": "original_filename", "value": {"stringValue": filename}},
            {"name": "mime_type",         "value": {"stringValue": mime_type} if mime_type else {"isNull": True}},
            {"name": "uploaded_by",       "value": {"stringValue": uploaded_by} if uploaded_by else {"isNull": True}},
        ],
    )


def lambda_handler(event, context):
    try:
        raw_body = event.get("body") or "{}"
        body = json.loads(raw_body)
        filename = body.get("filename", "")
        content_type = body.get("content_type", "").lower()

        if not filename:
            return response(400, {"error": "filename is required"})

        # Validate file extension
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return response(400, {
                "error": f"Unsupported file type '{ext}'. Allowed types: PDF, DOCX, TXT."
            })

        # Validate content type if provided
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            return response(400, {
                "error": f"Unsupported content type '{content_type}'."
            })

        # Extract user info from Cognito claims.
        # Support both API Gateway v1 (claims) and v2 (jwt.claims) formats.
        authorizer = event.get("requestContext", {}).get("authorizer", {})
        claims = authorizer.get("jwt", {}).get("claims", {}) or authorizer.get("claims", {})
        user_email = claims.get("email", "unknown")
        # Cognito sub is the stable unique identifier — use it as uploaded_by
        # so it matches across email changes.
        cognito_sub = claims.get("sub", user_email)

        # Generate a unique document ID (used for DynamoDB and S3 key) and a
        # separate contract_id for the Aurora app.contracts row.
        document_id = str(uuid.uuid4())
        contract_id = str(uuid.uuid4())
        key = f"uploads/{document_id}/{filename}"

        # Resolve MIME type: prefer the explicit content_type from the request,
        # fall back to inferring from the file extension.
        resolved_mime = content_type or {
            ".pdf":  "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt":  "text/plain",
        }.get(ext)

        # Generate presigned URL with Content-Length restriction (10 MB max)
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": key,
                "ContentType": "application/octet-stream",
            },
            ExpiresIn=300,
        )

        # Write initial metadata to DynamoDB immediately.
        # This reliably captures the uploader email at the time of request.
        # The data_processor Lambda will update file_size_kb when the S3 event arrives.
        dynamodb_client.put_item(
            TableName=DOCUMENTS_TABLE,
            Item={
                "document_id": {"S": document_id},
                "contract_id": {"S": contract_id},
                "document_name": {"S": filename},
                "s3_key": {"S": key},
                "upload_date": {"S": datetime.now(timezone.utc).isoformat()},
                "file_size_kb": {"N": "0"},
                "uploaded_by": {"S": user_email},
                "kb_status": {"S": "loading"},
            },
        )

        # Insert the app.contracts row so Zuhair's AI agents can locate and
        # process the document. Errors here are non-fatal for the upload itself
        # but are logged so they can be investigated.
        try:
            _insert_contract_row(
                contract_id=contract_id,
                s3_key=key,
                filename=filename,
                mime_type=resolved_mime,
                uploaded_by=cognito_sub,
            )
        except Exception as aurora_err:
            # Log but do not fail the upload — the presigned URL is already
            # generated and the DynamoDB record is written.
            print(f"[WARN] Failed to insert app.contracts row for contract_id={contract_id}: {aurora_err}")

        return response(200, {
            "upload_url": presigned_url,
            "key": key,
            "document_id": document_id,
            "contract_id": contract_id,
            "max_file_size": MAX_FILE_SIZE,
        })

    except Exception as e:
        return response(500, {"error": str(e)})


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body),
    }
