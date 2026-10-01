resource "aws_secretsmanager_secret" "cognito_secret" {
  for_each                = try(local.config.userPool.userPoolClient, {})
  name                    = "/cognito/${local.identifier}-${local.config.userPool.name}-client/pool-data"
  description             = "Cognito data for ${local.identifier}-${local.config.userPool.name} client & pool"
  kms_key_id              = aws_kms_key.cognito_secrets.arn
  recovery_window_in_days = each.value.secrets_recovery_window_in_days
}

resource "aws_secretsmanager_secret_version" "cognito_secret_version" {
  for_each  = try(local.config.userPool.userPoolClient, {})
  secret_id = aws_secretsmanager_secret.cognito_secret[each.key].id
  secret_string = jsonencode({
    user_pool_id  = aws_cognito_user_pool.pool.id
    client_id     = aws_cognito_user_pool_client.client[each.key].id
    client_secret = aws_cognito_user_pool_client.client[each.key].client_secret
    domain        = "https://${local.identifier}-${local.config.userPool.domain_prefix}.auth.${local.config.region}.amazoncognito.com"
    callback_url  = each.value.hostedUI.enabled ? join(", ", each.value.hostedUI.callback_urls) : ""
    scopes        = each.value.hostedUI.enabled ? join(" ", each.value.hostedUI.scopes) : ""
  })
}
