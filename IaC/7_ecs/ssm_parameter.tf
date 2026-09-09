resource "random_id" "secret" {
  # byte_length = 32 outputs a 64-character hex string
  # byte_length = 16 outputs a 32-character hex string
  byte_length = 16
}

resource "aws_ssm_parameter" "secret" {
  name        = "/${local.identifier}/ecs/secret"
  description = "Random secret for ${local.identifier} application"
  value       = random_id.secret.hex
  type        = "SecureString"
}
