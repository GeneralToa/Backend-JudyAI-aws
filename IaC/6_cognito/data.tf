data "aws_sesv2_email_identity" "ses" {
  count          = local.config.userPool.emailConfiguration.sendingAccount == "DEVELOPER" ? 1 : 0
  email_identity = local.config.userPool.emailConfiguration.domain
}

data "aws_ssm_parameter" "cloudfront_domain_name" {
  for_each = { for client_key, client in try(local.config.userPool.userPoolClient, {}) : client_key => client if try(client.hostedUI.cloudfrontDomain.enabled, false) == true }
  name     = "/${local.identifier}/cloudfront-${each.value.hostedUI.cloudfrontDomain.name}/domain-name"
}
