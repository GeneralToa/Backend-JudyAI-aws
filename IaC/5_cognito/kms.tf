resource "aws_kms_key" "cognito_secrets" {
  description  = "${local.identifier}-${local.config.userPool.name} Multi-region key for Cognito Pool"
  multi_region = true
}
