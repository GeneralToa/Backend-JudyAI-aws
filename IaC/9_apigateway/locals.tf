locals {
  config     = yamldecode(file("${path.module}/config/${terraform.workspace}.yaml"))
  identifier = "${local.config.identifier}-${terraform.workspace}"
  lambdaFunctions = {
    for lambda in flatten([
      for apigateway_key, apigateway_conf in try(local.config.apiGateway, {}) : [
        for lambda_key, lambda_conf in try(apigateway_conf.lambdaFunctions, {}) : {
          apigatewayKey = apigateway_key
          lambdaKey     = lambda_key
          lambdaName    = "${local.identifier}-${lambda_conf.functionName}"
        }
      ]
    ]) : "${lambda.apigatewayKey}-${lambda.lambdaKey}" => lambda
  }
  apiRoutes = {
    for route in flatten([
      for apigateway_key, apigateway_conf in try(local.config.apiGateway, {}) : [
        for lambda_key, lambda_conf in try(apigateway_conf.lambdaFunctions, {}) : {
          routeKey           = "${lambda_conf.method} ${lambda_conf.path}"
          authorization_type = lambda_conf.authRequired ? "JWT" : null
          authorizer_key     = lambda_conf.authRequired ? "cognito" : null
          integration = {
            uri                    = data.aws_lambda_function.lambda_function["${apigateway_key}-${lambda_key}"].arn
            method                 = try(lambda_conf.integrationMethod, null)
            payload_format_version = "2.0"
            timeout_milliseconds   = lambda_conf.timeoutMilliseconds
            type                   = try(lambda_conf.integrationType, "AWS_PROXY")
            request_templates      = try(lambda_conf.requestTemplates, {})
            response_parameters    = try(lambda_conf.responseParameters, null)
          }
        }
      ]
    ]) : route.routeKey => route
  }
}
