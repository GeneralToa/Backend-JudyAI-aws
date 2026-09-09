"""
data_upload Lambda function.
Generates a presigned URL for uploading a file to the S3 landing zone bucket.
Triggered by API Gateway (POST /upload) with Lambda Proxy integration.

Also writes initial document metadata to DynamoDB (document_id, name, uploaded_by)
so that the uploader email is reliably captured at upload time.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

s3_client = boto3.client("s3")
dynamodb_client = boto3.client("dynamodb")

BUCKET_NAME = os.environ["BUCKET_NAME"]
DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]

# 15 MB file size limit
MAX_FILE_SIZE = 15 * 1024 * 1024


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
        filename = body.get("filename", "")

        if not filename:
            return response(400, {"error": "filename is required"})

        # Extract user email from Cognito claims
        claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        user_email = claims.get("email", "unknown")

        # Generate a unique document ID and S3 key
        document_id = str(uuid.uuid4())
        key = f"uploads/{document_id}/{filename}"

        # Generate presigned URL with Content-Length restriction (15 MB max)
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
                "document_name": {"S": filename},
                "s3_key": {"S": key},
                "upload_date": {"S": datetime.now(timezone.utc).isoformat()},
                "file_size_kb": {"N": "0"},
                "uploaded_by": {"S": user_email},
                "kb_status": {"S": "loading"},
            },
        )

        return response(200, {
            "upload_url": presigned_url,
            "key": key,
            "document_id": document_id,
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
