data "aws_caller_identity" "caller_identity" {}

data "aws_cognito_user_pools" "cognito" {
  for_each = try(local.config.apiGateway, {})
  name     = "${local.identifier}-${each.value.authorizer.userPool}"
}

data "aws_cognito_user_pool_clients" "cognito_clients" {
  for_each     = try(local.config.apiGateway, {})
  user_pool_id = data.aws_cognito_user_pools.cognito[each.key].ids[0]
}

data "aws_cognito_user_pool" "cognito" {
  for_each     = try(local.config.apiGateway, {})
  user_pool_id = data.aws_cognito_user_pools.cognito[each.key].ids[0]
}

data "aws_lambda_function" "lambda_function" {
  for_each      = try(local.lambdaFunctions, {})
  function_name = each.value.lambdaName
}

data "aws_s3_bucket" "s3_bucket" {
  bucket = try(local.config.s3.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${local.config.s3.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${local.config.s3.name}"
}

# data "aws_acm_certificate" "certificate" {
#   for_each      = try(local.config.apiGateway,{})
#   domain        = each.value.certificateDomainName
# }
