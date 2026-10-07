data "aws_ssm_parameter" "apigateway_id" {
  name = "/${local.identifier}/apigateway/lambda-gateway-id"
}

data "aws_region" "us-east-1" {
  provider = aws.us-east-1
}
