resource "aws_ssm_parameter" "apigateway_id" {
  name  = "/${local.identifier}/apigateway/lambda-gateway-id"
  type  = "String"
  value = module.api_gateway["lambdaGateway"].api_id

  tags = {
    Name = "${local.identifier}-lambda-gateway-id"
  }
}
