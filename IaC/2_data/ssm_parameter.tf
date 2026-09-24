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

resource "aws_ssm_parameter" "data_automation_project_arn" {
  name  = "/${local.identifier}/bedrock/data-automation-project-arn"
  type  = "String"
  value = awscc_bedrock_data_automation_project.data_automation_project.project_arn

  tags = {
    Name = "${local.identifier}-bda-arn"
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

resource "aws_ssm_parameter" "aurora_postgres_arn" {
  name  = "/${local.identifier}/aurora/postgres-arn"
  type  = "String"
  value = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn

  tags = {
    Name = "${local.identifier}-aurora-postgres-arn"
  }
}

resource "aws_ssm_parameter" "kms_aurora_postgres" {
  name  = "/${local.identifier}/kms/aurora-postgres-arn"
  type  = "String"
  value = aws_kms_key.rdskey_aurora[local.config.bedrockKnowledgeBase.auroraDbKey].arn

  tags = {
    Name = "${local.identifier}-kms-aurora-postgres-arn"
  }
}

resource "aws_ssm_parameter" "judy_ai_writer_secret" {
  name  = "/${local.identifier}/secret-manager/judy-ai-writer-secret"
  type  = "String"
  value = aws_secretsmanager_secret.judy_ai_writer_secret.arn

  tags = {
    Name = "${local.identifier}-judy-ai-writer-secret-arn"
  }
}

resource "aws_ssm_parameter" "app_writer_secret" {
  name  = "/${local.identifier}/secret-manager/app-writer-secret"
  type  = "String"
  value = aws_secretsmanager_secret.app_writer_secret.arn

  tags = {
    Name = "${local.identifier}-app-writer-secret-arn"
  }
}
