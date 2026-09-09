resource "aws_kms_key" "rdskey_aurora" {
  for_each     = try(local.config.rdsAurora, {})
  description  = "${local.identifier}-${each.value.db_prefix} Multi-region key for Aurora RDS"
  multi_region = true
}

resource "aws_kms_key" "dynamodb" {
  description  = "${local.identifier} Multi-region key for DynamoDB"
  multi_region = true
}