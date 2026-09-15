resource "aws_ssm_parameter" "knowledge_base_id" {
  name  = "/${local.identifier}/bedrock/knowledge-base-id"
  type  = "String"
  value = aws_bedrockagent_knowledge_base.main.id

  tags = {
    Name = "${local.identifier}-kb-id"
  }
}

resource "aws_ssm_parameter" "data_source_id" {
  name  = "/${local.identifier}/bedrock/data-source-id"
  type  = "String"
  value = aws_bedrockagent_data_source.s3.data_source_id

  tags = {
    Name = "${local.identifier}-ds-id"
  }
}

resource "aws_ssm_parameter" "kms_dynamodb" {
  name  = "/${local.identifier}/kms/dynamodb-arn"
  type  = "String"
  value = aws_kms_key.dynamodb.arn

  tags = {
    Name = "${local.identifier}-kms-dynamodb-arn"
  }
}
