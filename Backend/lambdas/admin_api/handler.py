"""
admin_api Lambda function.
Handles the Admin Panel API for managing approval templates and system settings.

Routes:
    GET  /admin/templates              — List all approval templates
    PUT  /admin/templates/{templateId} — Update a template (name, routing_type, signer_roles, is_default)
    GET  /admin/settings               — Get system settings
    PUT  /admin/settings               — Save system settings

Aurora is accessed via the RDS Data API using the app_writer role.
Settings are stored in app.system_settings (key/value table, created if not exists).
"""

import json
import os
from datetime import datetime, timezone

import boto3

# --- Clients ---
rds_data_client = boto3.client("rds-data", region_name="us-west-2")
ssm_client = boto3.client("ssm", region_name="us-west-2")

# --- SSM parameter names ---
_AURORA_CLUSTER_ARN = None
_APP_WRITER_SECRET_ARN = None

# --- Aurora database name ---
AURORA_DATABASE = "ragdb"

# --- Default system settings ---
DEFAULT_SETTINGS = {
    "app_name": "Judy.ai",
    "max_file_size_mb": "10",
    "notify_document_assigned": "true",
    "notify_signature_completed": "true",
    "notify_risk_flagged": "false",
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


def ensure_settings_table():
    """Create app.system_settings table if it doesn't exist."""
    execute_sql("""
        CREATE TABLE IF NOT EXISTS app.system_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def lambda_handler(event, context):
    """Route event to appropriate handler based on HTTP method and path."""
    route_key = event.get("routeKey", "")
    path_params = event.get("pathParameters") or {}

    try:
        if route_key == "GET /admin/templates":
            return handle_list_templates()
        elif route_key == "PUT /admin/templates/{templateId}":
            return handle_update_template(path_params.get("templateId", ""), event)
        elif route_key == "GET /admin/settings":
            return handle_get_settings()
        elif route_key == "PUT /admin/settings":
            return handle_update_settings(event)
        else:
            return response(400, {"error": f"Unsupported route: {route_key}"})
    except Exception as e:
        print(f"Error handling {route_key}: {e}")
        return response(500, {"error": str(e)})


# =============================================================
# GET /admin/templates
# =============================================================
def handle_list_templates():
    """List all approval templates."""
    result = execute_sql(
        "SELECT id::text, name, routing_type, signer_roles, is_default, created_at FROM app.approval_templates ORDER BY created_at ASC"
    )

    templates = []
    for row in result.get("records", []):
        templates.append({
            "id":          row[0]["stringValue"],
            "name":        row[1]["stringValue"],
            "routingType": row[2]["stringValue"],
            "signerRoles": json.loads(row[3]["stringValue"]),
            "isDefault":   row[4]["booleanValue"],
            "createdAt":   row[5].get("stringValue", ""),
        })

    return response(200, {"templates": templates})


# =============================================================
# PUT /admin/templates/{templateId}
# =============================================================
def handle_update_template(template_id, event):
    """Update an approval template."""
    if not template_id:
        return response(400, {"error": "templateId is required"})

    body = json.loads(event.get("body") or "{}")
    name         = body.get("name")
    routing_type = body.get("routingType")
    signer_roles = body.get("signerRoles")
    is_default   = body.get("isDefault")

    # Check template exists
    result = execute_sql(
        "SELECT id FROM app.approval_templates WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": template_id}}]
    )
    if not result.get("records"):
        return response(404, {"error": "Template not found"})

    # Validate routing_type if provided
    if routing_type and routing_type not in ("sequential", "parallel"):
        return response(400, {"error": "routingType must be 'sequential' or 'parallel'"})

    # If setting as default, clear existing default first
    if is_default:
        execute_sql(
            "UPDATE app.approval_templates SET is_default = false WHERE is_default = true AND id != :id::uuid",
            [{"name": "id", "value": {"stringValue": template_id}}]
        )

    # Build update fields dynamically
    updates = []
    params = [{"name": "id", "value": {"stringValue": template_id}}]

    if name is not None:
        updates.append("name = :name")
        params.append({"name": "name", "value": {"stringValue": name}})
    if routing_type is not None:
        updates.append("routing_type = :routing_type")
        params.append({"name": "routing_type", "value": {"stringValue": routing_type}})
    if signer_roles is not None:
        updates.append("signer_roles = :signer_roles")
        params.append({"name": "signer_roles", "value": {"stringValue": json.dumps(signer_roles)}})
    if is_default is not None:
        updates.append("is_default = :is_default")
        params.append({"name": "is_default", "value": {"booleanValue": bool(is_default)}})

    if not updates:
        return response(400, {"error": "No fields to update"})

    execute_sql(
        f"UPDATE app.approval_templates SET {', '.join(updates)} WHERE id = :id::uuid",
        params
    )

    # Return updated template
    updated = execute_sql(
        "SELECT id::text, name, routing_type, signer_roles, is_default FROM app.approval_templates WHERE id = :id::uuid",
        [{"name": "id", "value": {"stringValue": template_id}}]
    )
    row = updated["records"][0]

    return response(200, {
        "id":          row[0]["stringValue"],
        "name":        row[1]["stringValue"],
        "routingType": row[2]["stringValue"],
        "signerRoles": json.loads(row[3]["stringValue"]),
        "isDefault":   row[4]["booleanValue"],
    })


# =============================================================
# GET /admin/settings
# =============================================================
def handle_get_settings():
    """Get all system settings, filling in defaults for missing keys."""
    ensure_settings_table()

    result = execute_sql("SELECT key, value FROM app.system_settings")

    settings = dict(DEFAULT_SETTINGS)  # start with defaults
    for row in result.get("records", []):
        settings[row[0]["stringValue"]] = row[1]["stringValue"]

    return response(200, {"settings": settings})


# =============================================================
# PUT /admin/settings
# =============================================================
def handle_update_settings(event):
    """Upsert system settings."""
    ensure_settings_table()

    body = json.loads(event.get("body") or "{}")
    settings = body.get("settings", {})

    if not settings:
        return response(400, {"error": "settings object is required"})

    # Validate known keys
    allowed_keys = set(DEFAULT_SETTINGS.keys())
    unknown = set(settings.keys()) - allowed_keys
    if unknown:
        return response(400, {"error": f"Unknown settings keys: {', '.join(unknown)}"})

    for key, value in settings.items():
        execute_sql(
            """
            INSERT INTO app.system_settings (key, value, updated_at)
            VALUES (:key, :value, now())
            ON CONFLICT (key) DO UPDATE SET value = :value, updated_at = now()
            """,
            [
                {"name": "key",   "value": {"stringValue": key}},
                {"name": "value", "value": {"stringValue": str(value)}},
            ]
        )

    return response(200, {"message": "Settings saved", "settings": settings})


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
