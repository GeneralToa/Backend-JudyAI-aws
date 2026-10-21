"""
signature_workflow Lambda function.
Handles the signature workflow engine for contract approval chains.
Triggered by API Gateway with Lambda Proxy integration.

Routes:
    POST /contracts/{contractId}/route          — Apply default template, assign signers,
                                                  update contract status to routed_for_signature
    GET  /contracts/{contractId}/signers         — Get list of signers and their status
    POST /contracts/{contractId}/sign            — Record a signer's signature. If all signers
                                                   done (sequential or parallel), insert
                                                   completed event and update status to signed.
    GET  /contracts/{contractId}/signed-copy     — Return presigned URL for signed PDF copy,
                                                   or 404 if not yet generated.

Async actions (invoked by other Lambdas, no routeKey):
    action=notify_risk_flagged                   — Send email when high risks are flagged
    action=generate_signed_copy                  — Build final signed PDF and upload to S3

Aurora is accessed via the RDS Data API using the app_writer role.
Cluster ARN and secret ARN are resolved from SSM at cold-start and cached.
"""

import base64
import io
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# --- Clients ---
rds_data_client = boto3.client("rds-data", region_name="us-west-2")
ssm_client = boto3.client("ssm", region_name="us-west-2")
lambda_client = boto3.client("lambda", region_name="us-west-2")
ses_client = boto3.client("sesv2", region_name="us-east-1")
dynamodb_client = boto3.client("dynamodb", region_name="us-west-2")
s3_client = boto3.client(
    "s3",
    region_name=os.environ.get("AWS_REGION", "us-west-2"),
    endpoint_url=f"https://s3.{os.environ.get('AWS_REGION', 'us-west-2')}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)

# --- Config ---
OBLIGATION_AGENT_FUNCTION_NAME = os.environ.get(
    "OBLIGATION_AGENT_FUNCTION_NAME",
    "rag-app-prod-agent-obligation-tracking",
)
SELF_FUNCTION_NAME = os.environ.get(
    "SELF_FUNCTION_NAME",
    "rag-app-prod-signature-workflow",
)
S3_BUCKET = os.environ.get("S3_BUCKET", "")
SES_FROM_ADDRESS = os.environ.get("SES_FROM_ADDRESS", "noreply@judy.ai")
SES_CONFIGURATION_SET = os.environ.get("SES_CONFIGURATION_SET", "")
APP_URL = os.environ.get("APP_URL", "https://d8xv5mej9ouxr.cloudfront.net")
DOCUMENTS_TABLE = os.environ.get("DOCUMENTS_TABLE", "rag-app-prod-documents")
SIGNED_COPY_URL_TTL = int(os.environ.get("SIGNED_COPY_URL_TTL", "900"))  # 15 min

# --- SSM parameter names ---
_AURORA_CLUSTER_ARN = None
_APP_WRITER_SECRET_ARN = None

# --- Aurora database name ---
AURORA_DATABASE = "ragdb"


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


def execute_sql(sql, parameters=None):
    """Execute a SQL statement via the RDS Data API."""
    _get_ssm_params()
    kwargs = {
        "resourceArn": _AURORA_CLUSTER_ARN,
        "secretArn": _APP_WRITER_SECRET_ARN,
        "database": AURORA_DATABASE,
        "sql": sql,
    }
    if parameters:
        kwargs["parameters"] = parameters
    return rds_data_client.execute_statement(**kwargs)


def execute_sql_json(sql, parameters=None):
    """Execute a SQL statement and return rows as list of dicts (formatRecordsAs=JSON)."""
    _get_ssm_params()
    kwargs = {
        "resourceArn": _AURORA_CLUSTER_ARN,
        "secretArn": _APP_WRITER_SECRET_ARN,
        "database": AURORA_DATABASE,
        "sql": sql,
        "formatRecordsAs": "JSON",
    }
    if parameters:
        kwargs["parameters"] = parameters
    result = rds_data_client.execute_statement(**kwargs)
    return json.loads(result.get("formattedRecords") or "[]")


def p(name, value):
    """Build a typed parameter for RDS Data API (JSON mode)."""
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}}


def get_uploader_email(contract_id):
    """
    Look up the uploader's email from DynamoDB using the contract_id.
    app.contracts.uploaded_by stores the Cognito sub, not the email.
    The email is in the DynamoDB documents table as uploaded_by.
    Returns None if not found.
    """
    try:
        scan_kwargs = {
            "TableName": DOCUMENTS_TABLE,
            "FilterExpression": "contract_id = :cid",
            "ExpressionAttributeValues": {":cid": {"S": contract_id}},
            "ProjectionExpression": "uploaded_by",
        }
        while True:
            result = dynamodb_client.scan(**scan_kwargs)
            items = result.get("Items", [])
            if items:
                return items[0].get("uploaded_by", {}).get("S")
            if "LastEvaluatedKey" not in result:
                break
            scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    except Exception as e:
        print(f"Warning: failed to look up uploader email for contract {contract_id}: {e}")
    return None


def _resolve_s3_bucket(contract_id):
    """
    Resolve the S3 bucket name. Prefers the S3_BUCKET env var (set by Terraform).
    Falls back to looking up the contract's s3_bucket column in Aurora.
    """
    if S3_BUCKET:
        return S3_BUCKET
    try:
        rows = execute_sql_json(
            "SELECT s3_bucket FROM app.contracts WHERE id = :id::uuid",
            [p("id", contract_id)],
        )
        if rows:
            return rows[0].get("s3_bucket", "")
    except Exception as e:
        print(f"Warning: could not resolve S3 bucket for contract {contract_id}: {e}")
    return ""


def _can_access_contract(contract_id, caller_sub, caller_email):
    """Return True if the caller is the uploader or a signer of the contract.

    Mirrors the same check used by GET /files and the analysis API.
    app.contracts.uploaded_by stores the Cognito sub (or email in older rows).
    Signers are matched by email, case-insensitive.
    """
    try:
        rows = execute_sql_json(
            "SELECT uploaded_by FROM app.contracts WHERE id = :id::uuid",
            [p("id", contract_id)],
        )
        if not rows:
            return False
        uploader = (rows[0].get("uploaded_by") or "").strip().lower()
        if uploader and uploader in (caller_sub, caller_email):
            return True
        if not caller_email:
            return False
        signer_rows = execute_sql_json(
            "SELECT 1 AS ok FROM app.contract_signers "
            "WHERE contract_id = :id::uuid AND lower(signer_email) = :email LIMIT 1",
            [p("id", contract_id), p("email", caller_email)],
        )
        return bool(signer_rows)
    except Exception as e:
        print(f"Warning: access check failed for contract {contract_id}: {e}")
        return False


def lambda_handler(event, context):
    """Route event to appropriate handler based on HTTP method and path."""
    route_key = event.get("routeKey", "")
    path_params = event.get("pathParameters") or {}
    contract_id = path_params.get("contractId", "")

    # Extract user info from Cognito claims
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("jwt", {}).get("claims", {}) or authorizer.get("claims", {})
    user_email = claims.get("email", "unknown")

    try:
        if route_key == "POST /contracts/{contractId}/route":
            return handle_route_contract(contract_id, user_email, event)
        elif route_key == "GET /contracts/{contractId}/signers":
            return handle_get_signers(contract_id)
        elif route_key == "POST /contracts/{contractId}/sign":
            return handle_sign(contract_id, user_email, event)
        elif route_key == "GET /contracts/{contractId}/signed-copy":
            return handle_get_signed_copy(contract_id, user_email, event)
        elif route_key == "POST /contracts/{contractId}/cancel-routing":
            return handle_cancel_routing(contract_id, user_email)
        else:
            # Check for async action invokes (from other Lambdas, no routeKey)
            body = json.loads(event.get("body") or "{}") if event.get("body") else event
            action = body.get("action")
            if action == "notify_risk_flagged":
                return handle_notify_risk_flagged(body)
            elif action == "generate_signed_copy":
                return handle_generate_signed_copy(body)
            return response(400, {"error": f"Unsupported route: {route_key}"})
    except Exception as e:
        print(f"Error handling {route_key}: {e}")
        return response(500, {"error": str(e)})


# =============================================================
# POST /contracts/{contractId}/route
# Apply the default template to a contract and assign signers.
# =============================================================
def handle_route_contract(contract_id, user_email, event):
    """Apply default approval template to a contract."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    # Check contract exists
    result = execute_sql(
        "SELECT id, status FROM app.contracts WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )
    if not result.get("records"):
        return response(404, {"error": "Contract not found"})

    current_status = result["records"][0][1]["stringValue"]
    if current_status not in ("uploaded", "analyzing", "ready_for_review"):
        return response(409, {"error": f"Contract cannot be routed from status: {current_status}"})

    # Get default template
    template_result = execute_sql(
        "SELECT id, routing_type, signer_roles FROM app.approval_templates WHERE is_default = true LIMIT 1"
    )
    if not template_result.get("records"):
        return response(404, {"error": "No default approval template found"})

    template = template_result["records"][0]
    template_id = template[0]["stringValue"]
    routing_type = template[1]["stringValue"]
    signer_roles = json.loads(template[2]["stringValue"])

    # Parse signers from request body — list of {email, role}
    body = json.loads(event.get("body") or "{}")
    signers = body.get("signers", [])

    if not signers:
        return response(400, {"error": "signers list is required. Provide [{email, role}] matching the template roles."})

    # Insert one row per signer into app.contract_signers
    for i, signer in enumerate(signers):
        execute_sql(
            """
            INSERT INTO app.contract_signers (
                contract_id, template_id, signer_email, signer_role, signer_order, status
            ) VALUES (
                :contract_id::uuid, :template_id::uuid, :signer_email, :signer_role, :signer_order, 'pending'
            )
            """,
            [
                {"name": "contract_id",   "value": {"stringValue": contract_id}},
                {"name": "template_id",   "value": {"stringValue": template_id}},
                {"name": "signer_email",  "value": {"stringValue": signer.get("email", "")}},
                {"name": "signer_role",   "value": {"stringValue": signer.get("role", "")}},
                {"name": "signer_order",  "value": {"longValue": i + 1}},
            ]
        )

    # Insert sent_for_signature event
    execute_sql(
        """
        INSERT INTO app.signature_events (contract_id, event_type, signer_email)
        VALUES (:contract_id::uuid, 'sent_for_signature', :signer_email)
        """,
        [
            {"name": "contract_id",  "value": {"stringValue": contract_id}},
            {"name": "signer_email", "value": {"stringValue": user_email}},
        ]
    )

    # Update contract status to routed_for_signature
    execute_sql(
        "UPDATE app.contracts SET status = 'routed_for_signature' WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )

    # Get contract name for email notifications
    contract_name_result = execute_sql(
        "SELECT original_filename FROM app.contracts WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )
    contract_name = contract_id  # fallback
    if contract_name_result.get("records"):
        contract_name = contract_name_result["records"][0][0]["stringValue"]

    # Notify each signer — fire and forget, must not fail the route response
    for signer in signers:
        notify_signer_assigned(
            signer_email=signer.get("email", ""),
            signer_role=signer.get("role", ""),
            contract_name=contract_name,
            contract_id=contract_id,
            signer_name=signer.get("name", "").strip() or None,
        )

    return response(200, {
        "contractId": contract_id,
        "templateId": template_id,
        "routingType": routing_type,
        "status": "routed_for_signature",
        "signers": signers,
    })


# =============================================================
# GET /contracts/{contractId}/signers
# Return list of signers and their current status.
# =============================================================
def handle_get_signers(contract_id):
    """Get signers and their status for a contract."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    result = execute_sql(
        """
        SELECT cs.id, cs.signer_email, cs.signer_role, cs.signer_order, cs.status,
               cs.assigned_at, cs.signed_at, at.routing_type
        FROM app.contract_signers cs
        LEFT JOIN app.approval_templates at ON at.id = cs.template_id
        WHERE cs.contract_id = :contract_id::uuid
        ORDER BY cs.signer_order ASC
        """,
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )

    signers = []
    routing_type = "sequential"  # safe default
    for row in result.get("records", []):
        signers.append({
            "signerId":    row[0]["stringValue"],
            "email":       row[1]["stringValue"],
            "role":        row[2]["stringValue"],
            "order":       row[3]["longValue"],
            "status":      row[4]["stringValue"],
            "assignedAt":  row[5].get("stringValue"),
            "signedAt":    row[6].get("stringValue") if not row[6].get("isNull") else None,
        })
        # All rows share the same template, grab routing_type from first non-null
        if not row[7].get("isNull"):
            routing_type = row[7]["stringValue"]

    return response(200, {
        "contractId":  contract_id,
        "routingType": routing_type,
        "signers":     signers,
    })


# =============================================================
# POST /contracts/{contractId}/sign
# Record a signer's signature. Check if all done and complete.
# =============================================================
def handle_sign(contract_id, user_email, event):
    """Record a signer's signature on a contract."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    body = json.loads(event.get("body") or "{}")
    signature_data = body.get("signatureData")  # base64 PNG
    field_id = body.get("fieldId")              # optional, from template-prepopulation
    page = body.get("page")                     # optional
    full_name = body.get("fullName", "").strip() or None
    title = body.get("title", "").strip() or None

    if not signature_data:
        return response(400, {"error": "signatureData is required"})

    # Find the signer record for this user
    signer_result = execute_sql(
        """
        SELECT id, status, signer_order
        FROM app.contract_signers
        WHERE contract_id = :contract_id::uuid AND signer_email = :email
        LIMIT 1
        """,
        [
            {"name": "contract_id", "value": {"stringValue": contract_id}},
            {"name": "email",       "value": {"stringValue": user_email}},
        ]
    )

    if not signer_result.get("records"):
        return response(404, {"error": "Signer not found for this contract"})

    signer_row = signer_result["records"][0]
    signer_id = signer_row[0]["stringValue"]
    signer_status = signer_row[1]["stringValue"]
    signer_order = signer_row[2]["longValue"]

    if signer_status == "signed":
        return response(409, {"error": "You have already signed this contract"})

    # Enforce turn order for sequential contracts
    routing_result = execute_sql(
        """
        SELECT at.routing_type
        FROM app.contract_signers cs
        JOIN app.approval_templates at ON at.id = cs.template_id
        WHERE cs.contract_id = :contract_id::uuid AND cs.signer_email = :email
        LIMIT 1
        """,
        [
            {"name": "contract_id", "value": {"stringValue": contract_id}},
            {"name": "email",       "value": {"stringValue": user_email}},
        ]
    )
    routing_type = "sequential"
    if routing_result.get("records"):
        rt = routing_result["records"][0][0]
        if not rt.get("isNull"):
            routing_type = rt["stringValue"]

    if routing_type == "sequential":
        earlier_pending = execute_sql(
            """
            SELECT COUNT(*) FROM app.contract_signers
            WHERE contract_id = :contract_id::uuid
              AND signer_order < :signer_order
              AND status = 'pending'
            """,
            [
                {"name": "contract_id",  "value": {"stringValue": contract_id}},
                {"name": "signer_order", "value": {"longValue": signer_order}},
            ]
        )
        if earlier_pending["records"][0][0]["longValue"] > 0:
            return response(403, {"error": "It is not your turn to sign. Earlier signers have not signed yet."})

    # Save the signature with full_name and title
    execute_sql(
        """
        INSERT INTO app.signer_signatures (
            contract_id, signer_id, signature_data, field_id, page, full_name, title
        ) VALUES (
            :contract_id::uuid,
            :signer_id::uuid,
            :signature_data,
            :field_id::uuid,
            :page,
            :full_name,
            :title
        )
        """,
        [
            {"name": "contract_id",    "value": {"stringValue": contract_id}},
            {"name": "signer_id",      "value": {"stringValue": signer_id}},
            {"name": "signature_data", "value": {"stringValue": signature_data}},
            {"name": "field_id",       "value": {"stringValue": field_id} if field_id else {"isNull": True}},
            {"name": "page",           "value": {"longValue": page} if page is not None else {"isNull": True}},
            {"name": "full_name",      "value": {"stringValue": full_name} if full_name else {"isNull": True}},
            {"name": "title",          "value": {"stringValue": title} if title else {"isNull": True}},
        ]
    )

    # Update signer status to signed
    execute_sql(
        """
        UPDATE app.contract_signers
        SET status = 'signed', signed_at = now()
        WHERE id = :signer_id::uuid
        """,
        [{"name": "signer_id", "value": {"stringValue": signer_id}}]
    )

    # Insert signer_confirmed event
    execute_sql(
        """
        INSERT INTO app.signature_events (contract_id, event_type, signer_email)
        VALUES (:contract_id::uuid, 'signer_confirmed', :email)
        """,
        [
            {"name": "contract_id", "value": {"stringValue": contract_id}},
            {"name": "email",       "value": {"stringValue": user_email}},
        ]
    )

    # Check if all signers have signed
    pending_result = execute_sql(
        """
        SELECT COUNT(*) FROM app.contract_signers
        WHERE contract_id = :contract_id::uuid AND status = 'pending'
        """,
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )
    pending_count = pending_result["records"][0][0]["longValue"]

    if pending_count == 0:
        # All signed — insert completed event and update contract status
        execute_sql(
            """
            INSERT INTO app.signature_events (contract_id, event_type, signer_email)
            VALUES (:contract_id::uuid, 'completed', :email)
            """,
            [
                {"name": "contract_id", "value": {"stringValue": contract_id}},
                {"name": "email",       "value": {"stringValue": user_email}},
            ]
        )

        execute_sql(
            """
            UPDATE app.contracts
            SET status = 'signed', signed_at = now()
            WHERE id = :id::uuid
            """,
            [{"name": "id", "value": {"stringValue": contract_id}}]
        )

        print(f"Contract {contract_id} fully signed — completed event inserted")

        # Notify the sender (uploaded_by) that all parties have signed
        try:
            contract_result = execute_sql(
                "SELECT original_filename FROM app.contracts WHERE id = :id::uuid",
                [{"name": "id", "value": {"stringValue": contract_id}}]
            )
            if contract_result.get("records"):
                contract_name = contract_result["records"][0][0]["stringValue"]
                sender_email  = get_uploader_email(contract_id)

                # Fetch all signers for the summary
                all_signers_result = execute_sql(
                    "SELECT signer_email, signer_role FROM app.contract_signers WHERE contract_id = :contract_id::uuid ORDER BY signer_order ASC",
                    [{"name": "contract_id", "value": {"stringValue": contract_id}}]
                )
                all_signers = [
                    {"email": r[0]["stringValue"], "role": r[1]["stringValue"]}
                    for r in all_signers_result.get("records", [])
                ]

                if sender_email:
                    notify_all_signed(sender_email, contract_name, all_signers)
        except Exception as e:
            print(f"Warning: failed to send completion notification for contract {contract_id}: {e}")

        # Fire obligation agent async — fire and forget
        try:
            lambda_client.invoke(
                FunctionName=OBLIGATION_AGENT_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({
                    "contract_id": contract_id,
                    "trigger": "signature_completed",
                }),
            )
            print(f"Obligation agent invoked for contract {contract_id}")
        except Exception as e:
            print(f"Warning: failed to invoke obligation agent for contract {contract_id}: {e}")

        # Trigger async PDF generation — fire and forget, must not fail the sign response
        try:
            lambda_client.invoke(
                FunctionName=SELF_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({
                    "action": "generate_signed_copy",
                    "contract_id": contract_id,
                }),
            )
            print(f"PDF generation triggered for contract {contract_id}")
        except Exception as e:
            print(f"Warning: failed to trigger PDF generation for contract {contract_id}: {e}")

        return response(200, {
            "contractId": contract_id,
            "status": "signed",
            "message": "All signers have signed. Contract is now complete.",
        })

    return response(200, {
        "contractId": contract_id,
        "status": "routed_for_signature",
        "message": "Signature recorded. Waiting for remaining signers.",
        "pendingSigners": pending_count,
    })


# =============================================================
# GET /contracts/{contractId}/signed-copy
# Return a presigned URL for the signed PDF, or 404 if not ready.
# =============================================================
def handle_get_signed_copy(contract_id, user_email, event):
    """Return presigned URL for the signed PDF copy."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    # Auth: only the uploader or a signer may fetch the signed copy
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("jwt", {}).get("claims", {}) or authorizer.get("claims", {})
    caller_sub   = (claims.get("sub")   or "").strip().lower()
    caller_email = (claims.get("email") or "").strip().lower()

    if not _can_access_contract(contract_id, caller_sub, caller_email):
        # Return 404 so contract IDs can't be probed
        return response(404, {"error": "Signed copy not yet available"})

    bucket = _resolve_s3_bucket(contract_id)
    if not bucket:
        return response(503, {"error": "Storage bucket not configured"})

    key = f"signed/{contract_id}.pdf"

    try:
        s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "403"):
            return response(404, {"error": "Signed copy not yet available"})
        print(f"Error checking signed copy for {contract_id}: {e}")
        return response(503, {"error": "Could not check signed copy availability"})

    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'inline; filename="signed-{contract_id}.pdf"',
            },
            ExpiresIn=SIGNED_COPY_URL_TTL,
        )
        return response(200, {"url": url, "contractId": contract_id})
    except Exception as e:
        print(f"Error generating presigned URL for {contract_id}: {e}")
        return response(500, {"error": "Could not generate download link"})


# =============================================================
# Async action: generate_signed_copy
# Invoked by handle_sign after the last signer. Builds the final
# signed PDF by stamping all signer data onto the page images and
# appending an audit trail page.
# =============================================================
def handle_generate_signed_copy(payload):
    """Build and upload the final signed PDF for a contract."""
    contract_id = payload.get("contract_id")
    if not contract_id:
        print("generate_signed_copy: missing contract_id")
        return {"statusCode": 400}

    print(f"generate_signed_copy: starting for contract {contract_id}")

    try:
        generate_signed_copy(contract_id)
        print(f"generate_signed_copy: completed for contract {contract_id}")
        return {"statusCode": 200}
    except Exception as e:
        # Failure is non-fatal — contract stays signed, Download serves original
        print(f"generate_signed_copy: FAILED for contract {contract_id}: {e}")
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "error": str(e)}


def generate_signed_copy(contract_id):
    """
    Core PDF generation logic.

    Steps:
    1. Fetch all signers + their signatures/name/title/signed_at
    2. Fetch field positions from template_prepopulation agent
    3. Fetch page images (JPEG) from S3
    4. For each page: open image with Pillow, stamp matching signer data at field coords
    5. Append audit trail page (built with reportlab)
    6. Combine all pages into a single PDF with pypdf
    7. Upload to signed/{contract_id}.pdf in S3
    """
    # --- lazy imports (only available in signature_workflow Lambda layer) ---
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfWriter, PdfReader
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    bucket = _resolve_s3_bucket(contract_id)
    if not bucket:
        raise RuntimeError("S3 bucket not configured")

    # ------------------------------------------------------------------ #
    # 1. Fetch signers + their signature data, full_name, title, signed_at #
    # ------------------------------------------------------------------ #
    signers_rows = execute_sql_json(
        """
        SELECT
            cs.id::text           AS signer_id,
            cs.signer_email       AS email,
            cs.signer_role        AS role,
            cs.signer_order       AS signer_order,
            cs.signed_at::text    AS signed_at,
            ss.signature_data,
            ss.full_name,
            ss.title
        FROM app.contract_signers cs
        LEFT JOIN app.signer_signatures ss ON ss.signer_id = cs.id
        WHERE cs.contract_id = :cid::uuid
        ORDER BY cs.signer_order ASC
        """,
        [p("cid", contract_id)],
    )

    if not signers_rows:
        raise RuntimeError("No signers found for contract")

    # ------------------------------------------------------------------ #
    # 2. Fetch field positions (template_prepopulation)                   #
    # ------------------------------------------------------------------ #
    fields_rows = execute_sql_json(
        """
        SELECT
            cf.id::text     AS field_id,
            cf.field_type,
            cf.label,
            cf.signer_role,
            cf.page,
            cf.position
        FROM judy_ai.contract_fields cf
        JOIN judy_ai.v_latest_agent_runs r
          ON r.run_id = cf.run_id
        WHERE r.contract_id = :cid::uuid
          AND r.agent_type   = 'template_prepopulation'
        ORDER BY cf.page NULLS LAST,
                 (cf.position->>'y')::float NULLS LAST,
                 (cf.position->>'x')::float
        """,
        [p("cid", contract_id)],
    )

    # Parse position JSON strings
    for row in fields_rows:
        pos = row.get("position")
        if isinstance(pos, str):
            try:
                row["position"] = json.loads(pos)
            except Exception:
                row["position"] = None

    # ------------------------------------------------------------------ #
    # 3. Fetch page images (JPEG) from S3                                 #
    # Falls back to the original PDF if page images don't exist           #
    # (PDF uploads — BDA does not rasterize them).                        #
    # ------------------------------------------------------------------ #
    extraction_rows = execute_sql_json(
        """
        SELECT raw_output_s3_key, page_count
        FROM judy_ai.document_extractions
        WHERE contract_id = :cid::uuid
          AND status = 'succeeded'
        ORDER BY completed_at DESC
        LIMIT 1
        """,
        [p("cid", contract_id)],
    )

    if not extraction_rows:
        raise RuntimeError("No successful extraction found for contract")

    extraction = extraction_rows[0]
    raw_key = extraction.get("raw_output_s3_key", "")
    page_count = int(extraction.get("page_count") or 0)

    if not raw_key.endswith("/result.json") or page_count < 1:
        raise RuntimeError(f"Unexpected raw_output_s3_key format or no pages: {raw_key}")

    # Build prefix: replace result.json with assets/rectified_image_
    prefix = raw_key[: -len("result.json")] + "assets/rectified_image_"

    # Look up original PDF S3 key for fallback rendering
    original_pdf_key = None
    try:
        pdf_rows = execute_sql_json(
            "SELECT s3_key FROM app.contracts WHERE id = :id::uuid",
            [p("id", contract_id)],
        )
        if pdf_rows:
            original_pdf_key = pdf_rows[0].get("s3_key")
    except Exception as e:
        print(f"Warning: could not fetch original PDF key for contract {contract_id}: {e}")

    # Pre-fetch original PDF bytes once if we might need them for fallback
    original_pdf_bytes = None
    if original_pdf_key:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=original_pdf_key)
            original_pdf_bytes = obj["Body"].read()
            print(f"Fetched original PDF ({len(original_pdf_bytes)} bytes) for fallback rendering")
        except Exception as e:
            print(f"Warning: could not fetch original PDF from S3 ({original_pdf_key}): {e}")

    # ------------------------------------------------------------------ #
    # 4. Build role → signer mapping                                      #
    # ------------------------------------------------------------------ #
    # Skip field types that are NOT stamped
    SKIP_FIELD_TYPES = {"initial", "company", "text"}

    # Build role → signer mapping (last signer per role wins)
    role_to_signer = {}
    for signer in signers_rows:
        role = (signer.get("role") or "").strip().lower()
        if role:
            role_to_signer[role] = signer

    # Identify the company signer and the non-company signer(s)
    company_signer = role_to_signer.get("company")
    other_signers = [s for s in signers_rows if (s.get("role") or "").strip().lower() != "company"]
    # Use the first non-company signer (lowest signer_order) as the "other party" signer
    other_signer = other_signers[0] if other_signers else None

    def match_signer_for_field(field):
        """Return the signer that should fill this field, or None.

        Rule:
          - field signer_role == "company" → company signer only.
            No company signer = return None (field stays empty).
          - field signer_role is anything else (vendor, supplier, advisor,
            recipient, etc.) → the non-company signer.
          - field signer_role is empty → non-company signer (page order).
        """
        field_role = (field.get("signer_role") or "").strip().lower()

        if field_role == "company":
            return company_signer  # None if no company signer — field stays empty

        # All other roles (including empty) go to the non-company signer
        return other_signer

    # ------------------------------------------------------------------ #
    # 5. Stamp each page                                                  #
    # ------------------------------------------------------------------ #

    pdf_writer = PdfWriter()

    for page_idx in range(page_count):
        page_num = page_idx + 1  # 1-indexed (matches field.page)

        # Download page JPEG from S3
        page_key = f"{prefix}{page_idx}.png"  # key name has .png extension (actually JPEG)
        img = None
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=page_key)
            img_bytes = obj["Body"].read()
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except ClientError:
            pass  # fall through to PDF fallback

        if img is None:
            # Page image not found — stamp directly onto the original PDF page
            if original_pdf_bytes:
                try:
                    page_fields = [
                        f for f in fields_rows
                        if f.get("page") == page_num
                        and f.get("position")
                    ]
                    stamped_page_bytes = _stamp_pdf_page_direct(
                        original_pdf_bytes, page_idx, page_fields,
                        signers_rows, match_signer_for_field,
                    )
                    page_reader = PdfReader(io.BytesIO(stamped_page_bytes))
                    pdf_writer.add_page(page_reader.pages[0])
                    print(f"Stamped page {page_num} directly onto original PDF page")
                    continue  # skip the image-based stamping below
                except Exception as e:
                    print(f"Warning: direct PDF stamp failed for page {page_num}: {e}")

            # Last resort: blank white page
            print(f"Warning: no image or PDF source for page {page_num}, using blank")
            img = Image.new("RGB", (2550, 3300), color=(255, 255, 255))

        img_w, img_h = img.size

        # Fields on this page (skipping ignored types)
        page_fields = [
            f for f in fields_rows
            if f.get("page") == page_num
            and f.get("field_type") not in SKIP_FIELD_TYPES
            and f.get("position")
        ]

        draw = ImageDraw.Draw(img)

        for field in page_fields:
            signer = match_signer_for_field(field)
            if not signer:
                continue

            pos = field["position"]
            # Normalized 0-1 coordinates, top-left origin
            x = float(pos.get("x", 0))
            y = float(pos.get("y", 0))
            w = float(pos.get("width", 0.1))
            h = float(pos.get("height", 0.05))

            # Convert to pixel coordinates
            py = int(y * img_h)
            ph = int(h * img_h)

            # Use AI x directly — it matches the purple field boxes the signer sees.
            # Clamp right edge so the stamp doesn't overflow the page.
            stamp_x = max(0, int(x * img_w))
            stamp_w = int(w * img_w)
            if stamp_x + stamp_w > img_w:
                stamp_w = img_w - stamp_x

            field_type = field.get("field_type", "")

            if field_type == "signature":
                sig_data = signer.get("signature_data")
                if sig_data:
                    _stamp_signature(draw, img, sig_data, stamp_x, py, stamp_w, ph)

            elif field_type == "full_name":
                name = signer.get("full_name") or signer.get("email", "")
                _stamp_text(draw, name, stamp_x, py, stamp_w, ph, img_w, img_h)

            elif field_type == "title":
                title_val = signer.get("title") or ""
                _stamp_text(draw, title_val, stamp_x, py, stamp_w, ph, img_w, img_h)

            elif field_type == "date":
                signed_at = signer.get("signed_at") or ""
                date_str = signed_at[:10] if signed_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                _stamp_text(draw, date_str, stamp_x, py, stamp_w, ph, img_w, img_h)

        # Convert stamped image to PDF page using reportlab
        page_pdf_bytes = _image_to_pdf_page(img)

        # Add to writer
        page_reader = PdfReader(io.BytesIO(page_pdf_bytes))
        pdf_writer.add_page(page_reader.pages[0])

    # ------------------------------------------------------------------ #
    # 6. Append audit trail page                                          #
    # ------------------------------------------------------------------ #
    audit_pdf_bytes = _build_audit_page(signers_rows, contract_id)
    audit_reader = PdfReader(io.BytesIO(audit_pdf_bytes))
    for audit_page in audit_reader.pages:
        pdf_writer.add_page(audit_page)

    # ------------------------------------------------------------------ #
    # 7. Upload to S3                                                     #
    # ------------------------------------------------------------------ #
    out_buf = io.BytesIO()
    pdf_writer.write(out_buf)
    out_buf.seek(0)

    signed_key = f"signed/{contract_id}.pdf"
    s3_client.put_object(
        Bucket=bucket,
        Key=signed_key,
        Body=out_buf.getvalue(),
        ContentType="application/pdf",
    )
    print(f"Signed PDF uploaded to s3://{bucket}/{signed_key}")


# ------------------------------------------------------------------ #
# Helpers — image stamping                                             #
# ------------------------------------------------------------------ #


def _stamp_pdf_page_direct(pdf_bytes, page_idx, fields_on_page, signers_rows,
                            match_signer_for_field):
    """Stamp signatures directly onto a PDF page without rasterizing.

    Used when page images are not available (PDF uploads). Draws stamp content
    as a reportlab PDF overlay and merges it onto the original page with pypdf.
    Returns a single-page PDF as bytes.
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas
    from PIL import Image

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if page_idx >= len(reader.pages):
        raise ValueError(f"Page index {page_idx} out of range")

    page = reader.pages[page_idx]
    media_box = page.mediabox
    page_w_pt = float(media_box.width)
    page_h_pt = float(media_box.height)

    # Build overlay PDF with stamps
    overlay_buf = io.BytesIO()
    c = rl_canvas.Canvas(overlay_buf, pagesize=(page_w_pt, page_h_pt))

    font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "font.ttf")
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont("CustomFont", font_path))
        overlay_font = "CustomFont"
    except Exception:
        overlay_font = "Helvetica"

    SKIP_FIELD_TYPES = {"initial", "company", "text"}

    for field in fields_on_page:
        if field.get("field_type") in SKIP_FIELD_TYPES:
            continue
        if not field.get("position"):
            continue

        signer = match_signer_for_field(field)
        if not signer:
            continue

        pos = field["position"]
        x = float(pos.get("x", 0))
        y = float(pos.get("y", 0))
        w = float(pos.get("width", 0.1))
        h = float(pos.get("height", 0.05))

        # Use AI x directly, clamp to page bounds
        stamp_x_pt = max(0.0, x * page_w_pt)
        stamp_w_pt = min(w * page_w_pt, page_w_pt - stamp_x_pt)

        # reportlab origin is bottom-left; convert from top-left normalized coords
        stamp_h_pt = h * page_h_pt
        stamp_y_pt = page_h_pt - (y * page_h_pt) - stamp_h_pt

        field_type = field.get("field_type", "")

        if field_type == "signature":
            sig_data = signer.get("signature_data") or ""
            if sig_data:
                try:
                    if "," in sig_data:
                        sig_data = sig_data.split(",", 1)[1]
                    sig_bytes = base64.b64decode(sig_data)
                    sig_img = Image.open(io.BytesIO(sig_bytes)).convert("RGBA")
                    # Make white/near-white pixels transparent
                    pixels = sig_img.load()
                    for sy in range(sig_img.height):
                        for sx in range(sig_img.width):
                            r, g, b, a = pixels[sx, sy]
                            if r > 230 and g > 230 and b > 230:
                                pixels[sx, sy] = (r, g, b, 0)
                    # Crop to drawn area so whitespace doesn't shrink the signature
                    bbox = sig_img.getbbox()
                    if bbox:
                        sig_img = sig_img.crop(bbox)
                    # Scale to fit stamp area preserving aspect ratio
                    sig_img.thumbnail((int(stamp_w_pt), int(stamp_h_pt)), Image.LANCZOS)
                    sig_buf = io.BytesIO()
                    sig_img.save(sig_buf, format="PNG")
                    sig_buf.seek(0)
                    from reportlab.lib.utils import ImageReader
                    c.drawImage(
                        ImageReader(sig_buf),
                        stamp_x_pt,
                        stamp_y_pt + (stamp_h_pt - sig_img.height) / 2,
                        width=sig_img.width,
                        height=sig_img.height,
                        mask="auto",
                    )
                except Exception as e:
                    print(f"Warning: could not draw signature overlay: {e}")

        else:
            if field_type == "full_name":
                text = signer.get("full_name") or signer.get("email", "")
            elif field_type == "title":
                text = signer.get("title") or ""
            elif field_type == "date":
                signed_at = signer.get("signed_at") or ""
                text = signed_at[:10] if signed_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                text = ""

            if text:
                font_size = max(8, int(stamp_h_pt * 0.7))
                c.setFont(overlay_font, font_size)
                c.setFillColorRGB(20 / 255, 20 / 255, 40 / 255)
                # Align text just above the underline (bottom of field box)
                c.drawString(stamp_x_pt, stamp_y_pt + stamp_h_pt * 0.15, text)

    c.save()
    overlay_buf.seek(0)

    # Merge overlay onto original page
    overlay_reader = PdfReader(overlay_buf)
    page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    writer.add_page(page)
    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


def _stamp_signature(draw, base_img, sig_data_b64, px, py, pw, ph):
    """Decode a base64 PNG signature and paste it onto the image.
    White pixels are made transparent so the document line shows through.
    """
    from PIL import Image
    try:
        # Remove data URL prefix if present
        if "," in sig_data_b64:
            sig_data_b64 = sig_data_b64.split(",", 1)[1]
        sig_bytes = base64.b64decode(sig_data_b64)
        sig_img = Image.open(io.BytesIO(sig_bytes)).convert("RGBA")

        # Make white (and near-white) pixels transparent
        pixels = sig_img.load()
        for y in range(sig_img.height):
            for x in range(sig_img.width):
                r, g, b, a = pixels[x, y]
                if r > 230 and g > 230 and b > 230:
                    pixels[x, y] = (r, g, b, 0)

        # Crop to drawn area so empty whitespace doesn't shrink the signature
        bbox = sig_img.getbbox()
        if bbox:
            sig_img = sig_img.crop(bbox)

        # Target size: field width x field height
        target_w = pw
        target_h = ph
        sig_img.thumbnail((target_w, target_h), Image.LANCZOS)

        # Position: centered vertically within the field
        paste_x = px
        paste_y = py + (ph - sig_img.height) // 2
        base_img.paste(sig_img, (paste_x, paste_y), sig_img)
        print(f"Stamped signature at ({paste_x},{paste_y}) size=({sig_img.width},{sig_img.height}) ph={ph}")
    except Exception as e:
        print(f"Warning: could not stamp signature: {e}")


def _stamp_text(draw, text, px, py, pw, ph, img_w, img_h):
    """Draw text onto the image at the field position using a bundled font."""
    from PIL import ImageFont
    import os
    if not text:
        return
    try:
        font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "font.ttf")

        # Scale font to field height — 70% fills the box naturally.
        # Shrinks if text is too wide for the field width.
        font_size = max(12, int(ph * 0.7))
        font = ImageFont.truetype(font_path, font_size)

        # Shrink font until text fits within available width
        while font_size > 12:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            if text_w <= pw:
                break
            font_size -= 2
            font = ImageFont.truetype(font_path, font_size)

        # Center text horizontally within the field box, vertically centered
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_x = px + (pw - text_w) // 2
        text_y = py + (ph - text_h) // 2

        draw.text((text_x, text_y), text, fill=(20, 20, 40), font=font)
        print(f"Stamped text '{text}' at ({text_x},{text_y}) font_size={font_size} ph={ph} pw={pw}")
    except Exception as e:
        print(f"Warning: could not stamp text '{text}': {e}")


def _image_to_pdf_page(img):
    """Convert a PIL Image to a single-page PDF bytes using reportlab canvas directly.
    Uses low-level canvas (not SimpleDocTemplate) to avoid margin/frame constraints.
    BDA rasterises at 150 dpi — use that for pt conversion so field overlays align.
    """
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader

    img_w, img_h = img.size

    # Convert PIL image to JPEG bytes
    img_buf = io.BytesIO()
    img.save(img_buf, format="JPEG", quality=92)
    img_buf.seek(0)

    # BDA rasterises at 300 dpi (2550×3300 = letter at 300dpi) — convert pixels → points (72 pt/inch)
    DPI = 300
    page_w_pt = img_w * 72 / DPI
    page_h_pt = img_h * 72 / DPI

    out_buf = io.BytesIO()
    c = rl_canvas.Canvas(out_buf, pagesize=(page_w_pt, page_h_pt))
    # Use ImageReader to wrap BytesIO — avoids os.path.splitext TypeError
    c.drawImage(ImageReader(img_buf), 0, 0, width=page_w_pt, height=page_h_pt)
    c.showPage()
    c.save()
    return out_buf.getvalue()


def _build_audit_page(signers_rows, contract_id):
    """Build a PDF audit trail page listing all signers."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    out_buf = io.BytesIO()
    doc = SimpleDocTemplate(out_buf, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AuditTitle",
        parent=styles["Heading1"],
        fontSize=18,
        spaceAfter=6,
        textColor=colors.HexColor("#1a1a2e"),
    )
    sub_style = ParagraphStyle(
        "AuditSub",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=18,
    )
    cell_style = ParagraphStyle(
        "AuditCell",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#1a1a2e"),
    )

    story = []
    story.append(Paragraph("Signature Audit Trail", title_style))
    story.append(Paragraph(
        f"Contract ID: {contract_id} &nbsp;·&nbsp; "
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        sub_style,
    ))

    # Table header
    table_data = [["Signer", "Role", "Signed At", "Signature"]]

    for signer in signers_rows:
        email = signer.get("email", "")
        role = signer.get("role", "")
        signed_at = (signer.get("signed_at") or "")[:19].replace("T", " ")
        full_name = signer.get("full_name") or ""
        title_val = signer.get("title") or ""
        sig_data = signer.get("signature_data") or ""

        signer_cell = Paragraph(
            f"<b>{email}</b><br/>"
            + (f"Name: {full_name}<br/>" if full_name else "")
            + (f"Title: {title_val}" if title_val else ""),
            cell_style,
        )

        sig_cell = ""
        if sig_data:
            try:
                if "," in sig_data:
                    sig_data = sig_data.split(",", 1)[1]
                sig_bytes = base64.b64decode(sig_data)
                sig_buf = io.BytesIO(sig_bytes)
                sig_img = RLImage(sig_buf, width=1.2 * inch, height=0.4 * inch)
                sig_cell = sig_img
            except Exception:
                sig_cell = Paragraph("(signature)", cell_style)

        table_data.append([
            signer_cell,
            Paragraph(role, cell_style),
            Paragraph(signed_at or "—", cell_style),
            sig_cell,
        ])

    col_widths = [2.5 * inch, 1.0 * inch, 1.5 * inch, 1.5 * inch]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#7c4dff")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 8),
        ("TOPPADDING",   (0, 0), (-1, 0), 8),
        # Data rows
        ("BACKGROUND",   (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9ff")]),
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 9),
        ("TOPPADDING",   (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 6),
        # Grid
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story.append(t)
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(
        "This audit trail was automatically generated by Judy.ai and records the "
        "digital signatures collected during the signing process.",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8,
                       textColor=colors.HexColor("#999999")),
    ))

    doc.build(story)
    return out_buf.getvalue()


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


# =============================================================
# Email Notifications via SES
# =============================================================
def send_email(to_address, subject, html_body):
    """Send an email via SES. Fails silently — never blocks the main flow."""
    try:
        ses_client.send_email(
            FromEmailAddress=SES_FROM_ADDRESS,
            Destination={"ToAddresses": [to_address]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": _strip_html(html_body), "Charset": "UTF-8"},
                    },
                }
            },
        )
        print(f"Email sent to {to_address}: {subject}")
    except Exception as e:
        print(f"Warning: failed to send email to {to_address}: {e}")


def _strip_html(html):
    """Very basic HTML stripper for plain text fallback."""
    import re
    return re.sub(r"<[^>]+>", "", html).strip()


def notify_signer_assigned(signer_email, signer_role, contract_name, contract_id, signer_name=None):
    """Notify a signer that a contract has been assigned to them."""
    contracts_url = f"{APP_URL}/index.html?section=contracts"
    subject = f"Action required: Contract sent to you for signature"
    greeting = f"Hi {signer_name}," if signer_name else "Hello,"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1a1a2e">
        <div style="background:#7c4dff;padding:24px 32px;border-radius:8px 8px 0 0">
            <h1 style="color:#fff;font-size:20px;margin:0">Judy.ai</h1>
        </div>
        <div style="background:#f9f9ff;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e0e0e0">
            <h2 style="font-size:18px;margin:0 0 12px">You have a contract to sign</h2>
            <p style="color:#444;line-height:1.6">
                {greeting} A contract has been sent to you for signature as <strong>{signer_role}</strong>.
            </p>
            <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:16px 0">
                <strong style="font-size:0.9rem">{contract_name}</strong>
            </div>
            <p style="color:#444;line-height:1.6">
                Please log in to review the document and add your signature.
            </p>
            <a href="{contracts_url}" style="display:inline-block;background:#7c4dff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
                Review &amp; Sign
            </a>
            <p style="color:#888;font-size:0.78rem;margin-top:24px">
                If you were not expecting this email, please ignore it.
            </p>
        </div>
    </div>
    """
    send_email(signer_email, subject, html)


def notify_all_signed(sender_email, contract_name, signers):
    """Notify the sender that all parties have signed."""
    contracts_url = f"{APP_URL}/index.html?section=contracts"
    signer_list = "".join([
        f"<li style='margin-bottom:4px'>✓ {s.get('email','')} <span style='color:#888;font-size:0.85rem'>({s.get('role','')})</span></li>"
        for s in signers
    ])
    subject = f"Contract fully signed: {contract_name}"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1a1a2e">
        <div style="background:#7c4dff;padding:24px 32px;border-radius:8px 8px 0 0">
            <h1 style="color:#fff;font-size:20px;margin:0">Judy.ai</h1>
        </div>
        <div style="background:#f9f9ff;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e0e0e0">
            <h2 style="font-size:18px;margin:0 0 12px">✅ Contract fully signed</h2>
            <p style="color:#444;line-height:1.6">
                All parties have signed <strong>{contract_name}</strong>.
            </p>
            <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:16px 0">
                <strong style="font-size:0.85rem;color:#555">Signers:</strong>
                <ul style="margin:8px 0 0;padding-left:20px;color:#444;font-size:0.88rem">
                    {signer_list}
                </ul>
            </div>
            <a href="{contracts_url}" style="display:inline-block;background:#7c4dff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
                View Contract
            </a>
        </div>
    </div>
    """
    send_email(sender_email, subject, html)


# =============================================================
# POST /contracts/{contractId}/cancel-routing
# Reset a stuck routed_for_signature contract back to ready_for_review.
# =============================================================
def handle_cancel_routing(contract_id, user_email):
    """Cancel routing for a contract — reset to ready_for_review."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    result = execute_sql(
        "SELECT id, status FROM app.contracts WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )
    if not result.get("records"):
        return response(404, {"error": "Contract not found"})

    current_status = result["records"][0][1]["stringValue"]
    if current_status not in ("routed_for_signature",):
        return response(409, {"error": f"Contract cannot be cancelled from status: {current_status}. Only routed_for_signature contracts can be cancelled."})

    execute_sql(
        "DELETE FROM app.contract_signers WHERE contract_id = :contract_id::uuid",
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )
    execute_sql(
        "DELETE FROM app.signature_events WHERE contract_id = :contract_id::uuid",
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )
    execute_sql(
        "UPDATE app.contracts SET status = 'ready_for_review' WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )

    print(f"Contract {contract_id} routing cancelled by {user_email}")

    return response(200, {
        "contractId": contract_id,
        "status": "ready_for_review",
        "message": "Routing cancelled. Contract is ready to be re-routed.",
    })


# =============================================================
# Async action: notify_risk_flagged
# =============================================================
def handle_notify_risk_flagged(payload):
    """Send email to contract uploader when high-severity risks are flagged."""
    contract_id = payload.get("contract_id")
    high_count  = payload.get("high_count", 0)

    if not contract_id:
        print("Warning: notify_risk_flagged called without contract_id")
        return {"statusCode": 400, "body": "contract_id required"}

    try:
        result = execute_sql(
            "SELECT original_filename FROM app.contracts WHERE id = :id::uuid",
            [{"name": "id", "value": {"stringValue": contract_id}}]
        )
        if not result.get("records"):
            print(f"Warning: contract {contract_id} not found for risk notification")
            return {"statusCode": 404, "body": "contract not found"}

        contract_name  = result["records"][0][0]["stringValue"]
        uploader_email = get_uploader_email(contract_id)

        if not uploader_email:
            print(f"Warning: no uploader email for contract {contract_id}")
            return {"statusCode": 200, "body": "no uploader email, skipped"}

        notify_risk_flagged(uploader_email, contract_name, contract_id, high_count)
        return {"statusCode": 200, "body": "notification sent"}

    except Exception as e:
        print(f"Warning: failed to send risk notification for contract {contract_id}: {e}")
        return {"statusCode": 500, "body": str(e)}


def notify_risk_flagged(uploader_email, contract_name, contract_id, high_count):
    """Send email to uploader when high-severity risks are detected."""
    review_url = f"{APP_URL}/review.html?contractId={contract_id}&name={contract_name}"
    subject = f"High-severity risks flagged in: {contract_name}"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1a1a2e">
        <div style="background:#7c4dff;padding:24px 32px;border-radius:8px 8px 0 0">
            <h1 style="color:#fff;font-size:20px;margin:0">Judy.ai</h1>
        </div>
        <div style="background:#f9f9ff;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e0e0e0">
            <h2 style="font-size:18px;margin:0 0 12px">⚠️ Risks flagged before signing</h2>
            <p style="color:#444;line-height:1.6">
                The AI analysis found <strong>{high_count} high-severity risk{'' if high_count == 1 else 's'}</strong> in:
            </p>
            <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:16px 0">
                <strong style="font-size:0.9rem">{contract_name}</strong>
            </div>
            <p style="color:#444;line-height:1.6">
                Please review the flagged risks before sending this contract for signature.
            </p>
            <a href="{review_url}" style="display:inline-block;background:#7c4dff;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
                Review Risks
            </a>
            <p style="color:#888;font-size:0.78rem;margin-top:24px">
                This is an automated notification from Judy.ai. Suggestions are advisory only and do not modify the document.
            </p>
        </div>
    </div>
    """
    send_email(uploader_email, subject, html)
