resource "aws_secretsmanager_secret" "aurora_secret" {
  for_each                = try(local.config.rdsAurora, {})
  name                    = "/rds/${local.identifier}-${each.value.db_prefix}-rds/aurora-password"
  description             = "Random password for ${local.identifier}-${each.value.db_prefix} Aurora RDS instance"
  kms_key_id              = aws_kms_key.rdskey_aurora[each.key].arn
  recovery_window_in_days = each.value.secrets_recovery_window_in_days
}

resource "aws_secretsmanager_secret_version" "aurora_secret_version" {
  for_each  = try(local.config.rdsAurora, {})
  secret_id = aws_secretsmanager_secret.aurora_secret[each.key].id
  secret_string = jsonencode({
    writer_endpoint = each.value.proxy_enabled ? module.rds_proxy[each.key].db_proxy_endpoints["read_write"].endpoint : module.aurora[each.key].cluster_endpoint
    reader_endpoint = each.value.proxy_enabled ? module.rds_proxy[each.key].db_proxy_endpoints["read_only"].endpoint : module.aurora[each.key].cluster_reader_endpoint
    writer_jdbc_url = "postgresql://${each.value.username}:${random_password.aurora_random_password[each.key].result}@${each.value.proxy_enabled ? module.rds_proxy[each.key].db_proxy_endpoints["read_write"].endpoint : module.aurora[each.key].cluster_endpoint}:${each.value.port}/${each.value.db_name}?schema=public"
    reader_jdbc_url = "postgresql://${each.value.username}:${random_password.aurora_random_password[each.key].result}@${each.value.proxy_enabled ? module.rds_proxy[each.key].db_proxy_endpoints["read_only"].endpoint : module.aurora[each.key].cluster_reader_endpoint}:${each.value.port}/${each.value.db_name}?schema=public"
    username        = each.value.username
    password        = random_password.aurora_random_password[each.key].result
  })
}

# ================== DB ROLE SECRETS ==================
resource "aws_secretsmanager_secret" "judy_ai_writer_secret" {
  name                    = "/rds/${local.identifier}-judy-ai-writer/credentials"
  description             = "Credentials for judy_ai_writer PostgreSQL role"
  kms_key_id              = aws_kms_key.rdskey_aurora[local.config.bedrockKnowledgeBase.auroraDbKey].arn
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "judy_ai_writer_secret_version" {
  secret_id = aws_secretsmanager_secret.judy_ai_writer_secret.id
  secret_string = jsonencode({
    username        = "judy_ai_writer"
    password        = random_password.judy_ai_writer_password.result
    writer_endpoint = local.config.rdsAurora[local.config.bedrockKnowledgeBase.auroraDbKey].proxy_enabled ? module.rds_proxy[local.config.bedrockKnowledgeBase.auroraDbKey].db_proxy_endpoints["read_write"].endpoint : module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_endpoint
    reader_endpoint = local.config.rdsAurora[local.config.bedrockKnowledgeBase.auroraDbKey].proxy_enabled ? module.rds_proxy[local.config.bedrockKnowledgeBase.auroraDbKey].db_proxy_endpoints["read_only"].endpoint : module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_reader_endpoint
    database        = "judy_ai"
  })
}

resource "aws_secretsmanager_secret" "app_writer_secret" {
  name                    = "/rds/${local.identifier}-app-writer/credentials"
  description             = "Credentials for app_writer PostgreSQL role"
  kms_key_id              = aws_kms_key.rdskey_aurora[local.config.bedrockKnowledgeBase.auroraDbKey].arn
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app_writer_secret_version" {
  secret_id = aws_secretsmanager_secret.app_writer_secret.id
  secret_string = jsonencode({
    username        = "app_writer"
    password        = random_password.app_writer_password.result
    writer_endpoint = local.config.rdsAurora[local.config.bedrockKnowledgeBase.auroraDbKey].proxy_enabled ? module.rds_proxy[local.config.bedrockKnowledgeBase.auroraDbKey].db_proxy_endpoints["read_write"].endpoint : module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_endpoint
    reader_endpoint = local.config.rdsAurora[local.config.bedrockKnowledgeBase.auroraDbKey].proxy_enabled ? module.rds_proxy[local.config.bedrockKnowledgeBase.auroraDbKey].db_proxy_endpoints["read_only"].endpoint : module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_reader_endpoint
    database        = "app"
  })
}
