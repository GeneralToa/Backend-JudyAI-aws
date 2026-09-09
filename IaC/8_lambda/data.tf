data "aws_caller_identity" "caller_identity" {}

data "aws_s3_bucket" "s3_bucket" {
  bucket = try(local.config.s3.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${local.config.s3.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${local.config.s3.name}"
}

data "aws_sqs_queue" "sqs_queue" {
  name = "${local.identifier}-${local.config.sqs.name}-sqs"
}

data "aws_dynamodb_table" "dynamodb_table" {
  name = "${local.identifier}-${local.config.dynamoDB.tableName}"
}

data "archive_file" "lambda_source" {
  for_each    = try(local.config.lambda, {})
  type        = "zip"
  source_file = "${path.module}/../../Backend/lambdas/${each.value.sourceCodePath}"
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
