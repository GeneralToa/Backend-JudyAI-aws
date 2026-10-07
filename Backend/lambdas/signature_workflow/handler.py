"""
signature_workflow Lambda function.
Handles the signature workflow engine for contract approval chains.
Triggered by API Gateway with Lambda Proxy integration.

Routes:
    POST /contracts/{contractId}/route   — Apply default template, assign signers,
                                           update contract status to routed_for_signature
    GET  /contracts/{contractId}/signers — Get list of signers and their status
    POST /contracts/{contractId}/sign    — Record a signer's signature. If all signers
                                           done (sequential or parallel), insert
                                           completed event and update status to signed.

Aurora is accessed via the RDS Data API using the app_writer role.
Cluster ARN and secret ARN are resolved from SSM at cold-start and cached.
"""

import json
import os
from datetime import datetime, timezone

import boto3

# --- Clients ---
rds_data_client = boto3.client("rds-data", region_name="us-west-2")
ssm_client = boto3.client("ssm", region_name="us-west-2")
lambda_client = boto3.client("lambda", region_name="us-west-2")
ses_client = boto3.client("sesv2", region_name="us-east-1")
dynamodb_client = boto3.client("dynamodb", region_name="us-west-2")

# --- Config ---
OBLIGATION_AGENT_FUNCTION_NAME = os.environ.get(
    "OBLIGATION_AGENT_FUNCTION_NAME",
    "rag-app-prod-agent-obligation-tracking",
)
SES_FROM_ADDRESS = os.environ.get("SES_FROM_ADDRESS", "noreply@judy.ai")
SES_CONFIGURATION_SET = os.environ.get("SES_CONFIGURATION_SET", "")  # leave empty until config set exists in us-east-1
APP_URL = os.environ.get("APP_URL", "https://d8xv5mej9ouxr.cloudfront.net")
DOCUMENTS_TABLE = os.environ.get("DOCUMENTS_TABLE", "rag-app-prod-documents")

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
        elif route_key == "POST /contracts/{contractId}/cancel-routing":
            return handle_cancel_routing(contract_id, user_email)
        else:
            # Check for async action invokes (from other Lambdas, no routeKey)
            body = json.loads(event.get("body") or "{}") if event.get("body") else event
            action = body.get("action")
            if action == "notify_risk_flagged":
                return handle_notify_risk_flagged(body)
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
    field_id = body.get("fieldId")              # optional, from Zuhair's template-prepopulation
    page = body.get("page")                     # optional

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

    # Save the signature
    execute_sql(
        """
        INSERT INTO app.signer_signatures (contract_id, signer_id, signature_data, field_id, page)
        VALUES (
            :contract_id::uuid,
            :signer_id::uuid,
            :signature_data,
            :field_id::uuid,
            :page
        )
        """,
        [
            {"name": "contract_id",    "value": {"stringValue": contract_id}},
            {"name": "signer_id",      "value": {"stringValue": signer_id}},
            {"name": "signature_data", "value": {"stringValue": signature_data}},
            {"name": "field_id",       "value": {"stringValue": field_id} if field_id else {"isNull": True}},
            {"name": "page",           "value": {"longValue": page} if page else {"isNull": True}},
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

        # Fire obligation agent async — fire and forget, must not fail the sign response
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
            # ConfigurationSetName intentionally omitted — config set doesn't exist in us-east-1 yet
        )
        print(f"Email sent to {to_address}: {subject}")
    except Exception as e:
        print(f"Warning: failed to send email to {to_address}: {e}")


def _strip_html(html):
    """Very basic HTML stripper for plain text fallback."""
    import re
    return re.sub(r"<[^>]+>", "", html).strip()


def notify_signer_assigned(signer_email, signer_role, contract_name, contract_id):
    """Notify a signer that a contract has been assigned to them."""
    contracts_url = f"{APP_URL}/index.html?section=contracts"
    subject = f"Action required: Contract sent to you for signature"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1a1a2e">
        <div style="background:#7c4dff;padding:24px 32px;border-radius:8px 8px 0 0">
            <h1 style="color:#fff;font-size:20px;margin:0">Judy.ai</h1>
        </div>
        <div style="background:#f9f9ff;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e0e0e0">
            <h2 style="font-size:18px;margin:0 0 12px">You have a contract to sign</h2>
            <p style="color:#444;line-height:1.6">
                A contract has been sent to you for signature as <strong>{signer_role}</strong>.
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
# Deletes all signer rows and signature events for the contract.
# =============================================================
def handle_cancel_routing(contract_id, user_email):
    """Cancel routing for a contract — reset to ready_for_review."""
    if not contract_id:
        return response(400, {"error": "contractId is required"})

    # Check contract exists and is in a routable state
    result = execute_sql(
        "SELECT id, status FROM app.contracts WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": contract_id}}]
    )
    if not result.get("records"):
        return response(404, {"error": "Contract not found"})

    current_status = result["records"][0][1]["stringValue"]
    if current_status not in ("routed_for_signature",):
        return response(409, {"error": f"Contract cannot be cancelled from status: {current_status}. Only routed_for_signature contracts can be cancelled."})

    # Delete signer rows
    execute_sql(
        "DELETE FROM app.contract_signers WHERE contract_id = :contract_id::uuid",
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )

    # Delete signature events
    execute_sql(
        "DELETE FROM app.signature_events WHERE contract_id = :contract_id::uuid",
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )

    # Reset contract status to ready_for_review
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
# Invoked by agent-risk-clause when high-severity risks are found.
# Payload: {"action": "notify_risk_flagged", "contract_id": "...", "high_count": n}
# =============================================================
def handle_notify_risk_flagged(payload):
    """Send email to contract uploader when high-severity risks are flagged."""
    contract_id = payload.get("contract_id")
    high_count  = payload.get("high_count", 0)

    if not contract_id:
        print("Warning: notify_risk_flagged called without contract_id")
        return {"statusCode": 400, "body": "contract_id required"}

    # Get contract name and uploader email
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
