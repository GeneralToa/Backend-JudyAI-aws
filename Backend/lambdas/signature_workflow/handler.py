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

# --- Config ---
OBLIGATION_AGENT_FUNCTION_NAME = os.environ.get(
    "OBLIGATION_AGENT_FUNCTION_NAME",
    "rag-app-prod-agent-obligation-tracking",
)

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
        else:
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
        SELECT id, signer_email, signer_role, signer_order, status, assigned_at, signed_at
        FROM app.contract_signers
        WHERE contract_id = :contract_id::uuid
        ORDER BY signer_order ASC
        """,
        [{"name": "contract_id", "value": {"stringValue": contract_id}}]
    )

    signers = []
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

    return response(200, {
        "contractId": contract_id,
        "signers": signers,
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

    if signer_status == "signed":
        return response(409, {"error": "You have already signed this contract"})

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
