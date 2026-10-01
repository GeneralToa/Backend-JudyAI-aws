# =============== LAMBDA CLOUDWATCH LOG GROUPS ===============
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  for_each          = try(local.config.lambda, {})
  name              = "/aws/lambda/${each.value.functionName}"
  retention_in_days = 30
}