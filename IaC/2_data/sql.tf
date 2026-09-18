# ================== BEDROCK VECTOR DB MIGRATION ==================
resource "null_resource" "bedrock_kb_schema" {
  triggers = {
    cluster_arn = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn
    sql_hash    = filemd5("${path.module}/scripts/000_setup_vector_db.sql")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e

      run_sql() {
        aws rds-data execute-statement \
          --region "${local.config.region}" \
          --resource-arn "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn}" \
          --secret-arn "${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn}" \
          --database "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name}" \
          --sql "$1"
      }

      # DDL — execute each statement from the migration file
      while IFS= read -r -d ';' stmt || [ -n "$stmt" ]; do
        trimmed=$(printf '%s' "$stmt" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        content=$(printf '%s' "$trimmed" | sed 's/--[^\n]*//g' | tr -d '[:space:]')
        [ -z "$content" ] && continue
        run_sql "$trimmed"
      done < "${path.module}/scripts/setup_vector_db.sql"

      # Role (idempotent)
      run_sql "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$BEDROCK_USERNAME') THEN EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '$BEDROCK_USERNAME', '$BEDROCK_PASSWORD'); END IF; END \$\$"

      # Grants
      run_sql "DO \$\$ BEGIN EXECUTE format('GRANT ALL ON SCHEMA bedrock_integration TO %I', '$BEDROCK_USERNAME'); END \$\$"
      run_sql "DO \$\$ BEGIN EXECUTE format('GRANT ALL ON TABLE bedrock_integration.bedrock_kb TO %I', '$BEDROCK_USERNAME'); END \$\$"
    EOT

    environment = {
      BEDROCK_USERNAME = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_master_username
      BEDROCK_PASSWORD = random_password.aurora_random_password[local.config.bedrockKnowledgeBase.auroraDbKey].result
    }
  }

  depends_on = [
    module.aurora,
    aws_secretsmanager_secret_version.aurora_secret_version
  ]
}

# ================== AI SCHEMA MIGRATION ==================
resource "random_password" "judy_ai_writer_password" {
  length           = 40
  special          = false
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "app_writer_password" {
  length           = 40
  special          = false
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "null_resource" "ai_schema_migration" {
  triggers = {
    cluster_arn = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn
    sql_hash    = filemd5("${path.module}/scripts/001_ai_schema.sql")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e

      run_sql() {
        aws rds-data execute-statement \
          --region "${local.config.region}" \
          --resource-arn "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn}" \
          --secret-arn "${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn}" \
          --database "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name}" \
          --sql "$1"
      }

      # DDL — execute each statement from the migration file
      while IFS= read -r -d ';' stmt || [ -n "$stmt" ]; do
        trimmed=$(printf '%s' "$stmt" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        # Skip empty or comment-only blocks
        content=$(printf '%s' "$trimmed" | sed 's/--[^\n]*//g' | tr -d '[:space:]')
        [ -z "$content" ] && continue
        run_sql "$trimmed"
      done < "${path.module}/scripts/001_ai_schema.sql"

      # Roles — idempotent via DO $$ block, same pattern as bedrock_kb_schema
      run_sql "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'judy_ai_writer') THEN EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', 'judy_ai_writer', '$JUDY_AI_WRITER_PASSWORD'); END IF; END \$\$"
      run_sql "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_writer') THEN EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', 'app_writer', '$APP_WRITER_PASSWORD'); END IF; END \$\$"

      # Grants
      run_sql "GRANT USAGE ON SCHEMA judy_ai TO judy_ai_writer, app_writer"
      run_sql "GRANT USAGE ON SCHEMA app TO judy_ai_writer, app_writer"
      run_sql "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA judy_ai TO judy_ai_writer"
      run_sql "GRANT SELECT ON ALL TABLES IN SCHEMA app TO judy_ai_writer"
      run_sql "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA app TO app_writer"
      run_sql "GRANT SELECT ON ALL TABLES IN SCHEMA judy_ai TO app_writer"
    EOT

    environment = {
      JUDY_AI_WRITER_PASSWORD = random_password.judy_ai_writer_password.result
      APP_WRITER_PASSWORD     = random_password.app_writer_password.result
    }
  }

  depends_on = [
    null_resource.bedrock_kb_schema,
    aws_secretsmanager_secret_version.judy_ai_writer_secret_version,
    aws_secretsmanager_secret_version.app_writer_secret_version
  ]
}

resource "null_resource" "risk_category_tracked_term" {
  triggers = {
    cluster_arn = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn
    sql_hash    = filemd5("${path.module}/scripts/002_risk_category_tracked_term.sql")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e

      run_sql() {
        aws rds-data execute-statement \
          --region "${local.config.region}" \
          --resource-arn "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn}" \
          --secret-arn "${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn}" \
          --database "${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name}" \
          --sql "$1"
      }

      # DDL — execute each statement from the migration file
      while IFS= read -r -d ';' stmt || [ -n "$stmt" ]; do
        trimmed=$(printf '%s' "$stmt" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        # Skip empty or comment-only blocks
        content=$(printf '%s' "$trimmed" | sed 's/--[^\n]*//g' | tr -d '[:space:]')
        [ -z "$content" ] && continue
        run_sql "$trimmed"
      done < "${path.module}/scripts/002_risk_category_tracked_term.sql"

    EOT
  }

  depends_on = [
    null_resource.ai_schema_migration,
    aws_secretsmanager_secret_version.aurora_secret_version
  ]
}
