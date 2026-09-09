data "aws_sesv2_email_identity" "ses" {
  count          = local.config.userPool.emailConfiguration.sendingAccount == "DEVELOPER" ? 1 : 0
  email_identity = local.config.userPool.emailConfiguration.domain
}
