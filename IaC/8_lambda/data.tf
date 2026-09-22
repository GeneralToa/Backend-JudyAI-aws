data "aws_caller_identity" "caller_identity" {}

data "aws_s3_bucket" "s3_bucket" {
  bucket = try(local.config.s3.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${local.config.s3.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${local.config.s3.name}"
}

data "aws_sqs_queue" "sqs_queue" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, {}) : lambda_key => lambda_conf
    if try(lambda_conf.eventSourceMapping.enabled, false) && try(lambda_conf.eventSourceMapping.type, "") == "sqs"
  }
  name = "${local.identifier}-${each.value.eventSourceMapping.name}-sqs"
}

data "aws_dynamodb_table" "dynamodb_table" {
  name = "${local.identifier}-${local.config.dynamoDB.tableName}"
}

data "archive_file" "lambda_source" {
  for_each    = try(local.config.lambda, {})
  type        = "zip"
  source_dir  = "${path.module}/../../Backend/lambdas/${each.value.sourceCodePath}"
  output_path = "${path.module}/functions/${each.value.outputCodePath}"
}

data "aws_vpc" "vpc" {
  filter {
    name   = "tag:Env"
    values = [terraform.workspace]
  }
}

data "aws_subnets" "private_subnets" {
  filter {
    name   = "tag:Env"
    values = [terraform.workspace]
  }

  filter {
    name   = "tag:layer"
    values = ["private"]
  }

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.vpc.id]
  }
}

data "aws_security_group" "lambda_sg" {
  name   = "${local.identifier}-lambda-sg"
  vpc_id = data.aws_vpc.vpc.id
}

data "aws_ssm_parameter" "knowledge_base_id" {
  name = "/${local.identifier}/bedrock/knowledge-base-id"
}

data "aws_ssm_parameter" "data_source_id" {
  name = "/${local.identifier}/bedrock/data-source-id"
}

data "aws_ssm_parameter" "kms_dynamodb_arn" {
  name = "/${local.identifier}/kms/dynamodb-arn"
}

data "aws_ssm_parameter" "aurora_postgres_arn" {
  name = "/${local.identifier}/aurora/postgres-arn"
}

data "aws_ssm_parameter" "kms_aurora_postgres_arn" {
  name = "/${local.identifier}/kms/aurora-postgres-arn"
}

data "aws_ssm_parameter" "judy_ai_writer_secret_arn" {
  name = "/${local.identifier}/secret-manager/judy-ai-writer-secret"
}

data "aws_ssm_parameter" "app_writer_secret_arn" {
  name = "/${local.identifier}/secret-manager/app-writer-secret"
}
