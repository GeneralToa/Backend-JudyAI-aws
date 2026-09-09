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
