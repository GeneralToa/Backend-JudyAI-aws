module "api_gateway" {
  for_each = try(local.config.apiGateway, {})
  source   = "terraform-aws-modules/apigateway-v2/aws"
  version  = "6.1.0"

  name          = "${local.identifier}-${each.value.name}"
  description   = each.value.description
  protocol_type = each.value.protocolType
  # Custom domain
  create_domain_records = can(each.value.hostedZoneName) ? true : false
  hosted_zone_name      = try(each.value.hostedZoneName, null)
  create_domain_name    = can(each.value.domainName) ? true : false
  domain_name           = try(each.value.domainName, "")
  #domain_name_certificate_arn   = try(data.aws_acm_certificate.certificate[each.key].arn, null)
  create_certificate = false
  cors_configuration = each.value.corsConfiguration


  # Access logs
  stage_access_log_settings = {
    create_log_group            = true
    log_group_retention_in_days = 7
    format = jsonencode({
      context = {
        domainName              = "$context.domainName"
        integrationErrorMessage = "$context.integrationErrorMessage"
        protocol                = "$context.protocol"
        requestId               = "$context.requestId"
        requestTime             = "$context.requestTime"
        responseLength          = "$context.responseLength"
        routeKey                = "$context.routeKey"
        stage                   = "$context.stage"
        status                  = "$context.status"
        error = {
          message      = "$context.error.message"
          responseType = "$context.error.responseType"
        }
        identity = {
          sourceIP = "$context.identity.sourceIp"
        }
        integration = {
          error             = "$context.integration.error"
          integrationStatus = "$context.integration.integrationStatus"
        }
      }
    })
  }
  # Authorizer(s)
  authorizers = each.value.authorizer.enabled ? {
    cognito = {
      authorizer_type  = "JWT"
      identity_sources = ["$request.header.Authorization"]
      name             = each.value.authorizer.name
      jwt_configuration = {
        audience = data.aws_cognito_user_pool_clients.cognito_clients[each.key].client_ids
        issuer   = "https://cognito-idp.${local.config.region}.amazonaws.com/${data.aws_cognito_user_pools.cognito[each.key].ids[0]}"
      }
    }
  } : {}

  # Routes & Integration(s)
  routes = local.apiRoutes
}

resource "aws_lambda_permission" "allow_api_gateway" {
  for_each = {
    for lambda_key, lambda_conf in try(local.lambdaFunctions, {}) : lambda_conf.lambdaName => lambda_conf... #Group values with same key because there are 2 routes with the same functionName.
  }
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = each.value[0].lambdaName
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${module.api_gateway[each.value[0].apigatewayKey].api_execution_arn}/*/*"
}
