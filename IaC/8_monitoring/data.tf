data "aws_ssm_parameter" "apigateway_id" {
  name = "/${local.identifier}/apigateway/lambda-gateway-id"
}
