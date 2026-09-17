# ================= LAMBDA FUNCTIONS =================
resource "aws_lambda_layer_version" "lambda_layer" {
  count                    = local.config.lambdaLayer.enabled ? 1 : 0
  filename                 = "${path.module}/functions/dependencies_layer/${local.config.lambdaLayer.fileName}"
  layer_name               = "${local.identifier}-lambda-dependencies"
  description              = "Common dependencies for Lambda functions"
  compatible_runtimes      = local.config.lambdaLayer.compatibleRuntimes
  compatible_architectures = local.config.lambdaLayer.compatibleArchitectures
}

resource "aws_lambda_function" "lambda" {
  for_each      = try(local.config.lambda, {})
  filename      = data.archive_file.lambda_source[each.key].output_path
  function_name = "${local.identifier}-${each.value.functionName}"
  role          = aws_iam_role.lambda_role[each.key].arn
  handler       = each.value.handler
  code_sha256   = data.archive_file.lambda_source[each.key].output_base64sha256
  timeout       = each.value.timeout
  memory_size   = try(each.value.memorySize, 128)

  layers  = each.value.lambdaLayer.enabled ? (local.config.lambdaLayer.enabled ? concat(aws_lambda_layer_version.lambda_layer[0].arn, each.value.lambdaLayer.arns) : each.value.lambdaLayer.arns) : local.config.lambdaLayer.enabled ? aws_lambda_layer_version.lambda_layer[0].arn : []
  runtime = each.value.runtime

  dynamic "environment" {
    for_each = each.value.role == "dataUpload" ? [1] : []
    content {
      variables = {
        BUCKET_NAME     = try(local.config.s3.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${local.config.s3.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${local.config.s3.name}"
        DOCUMENTS_TABLE = "${local.identifier}-${local.config.dynamoDB.tableName}"
      }
    }
  }

  dynamic "environment" {
    for_each = each.value.role == "dataProcessor" ? [1] : []
    content {
      variables = {
        KB_ID_PARAM_NAME             = data.aws_ssm_parameter.knowledge_base_id.name
        DS_ID_PARAM_NAME             = data.aws_ssm_parameter.data_source_id.name
        DOCUMENTS_TABLE              = "${local.identifier}-${local.config.dynamoDB.tableName}"
        LANDING_ZONE_BUCKET          = try(local.config.s3.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${local.config.s3.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${local.config.s3.name}"
        INGESTION_POST_PROCESSOR_ARN = "arn:aws:lambda:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:function:${local.identifier}-ingestion-post-processor"
        RESOURCE_PREFIX              = local.identifier
        SCHEDULER_ROLE_ARN           = aws_iam_role.scheduler_execution.arn
      }
    }
  }

  dynamic "environment" {
    for_each = each.value.role == "retrieveAndGenerate" ? [1] : []
    content {
      variables = {
        KNOWLEDGE_BASE_ID    = data.aws_ssm_parameter.knowledge_base_id.value
        FOUNDATION_MODEL_ARN = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:inference-profile/${local.config.bedrockKnowledgeBase.bedrockInferenceProfileId}"
      }
    }
  }

  dynamic "environment" {
    for_each = each.value.role == "ingestionPostProcessor" ? [1] : []
    content {
      variables = {
        KB_ID_PARAM_NAME             = data.aws_ssm_parameter.knowledge_base_id.name
        DS_ID_PARAM_NAME             = data.aws_ssm_parameter.data_source_id.name
        DOCUMENTS_TABLE              = "${local.identifier}-${local.config.dynamoDB.tableName}"
        INGESTION_POST_PROCESSOR_ARN = "arn:aws:lambda:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:function:${local.identifier}-ingestion-post-processor"
        RESOURCE_PREFIX              = local.identifier
        SCHEDULER_ROLE_ARN           = aws_iam_role.scheduler_execution.arn
      }
    }
  }

  dynamic "vpc_config" {
    for_each = try(each.value.vpcConfig, false) ? [1] : []
    content {
      subnet_ids         = data.aws_subnets.private_subnets.ids
      security_group_ids = [data.aws_security_group.lambda_sg.id]
    }
  }

  tags = {
    Application = "${local.identifier}-${each.value.functionName}"
  }

  depends_on = [aws_cloudwatch_log_group.lambda_log_group]
}

# ================ LAMBDA SQS POLLING ================
resource "aws_lambda_event_source_mapping" "sqs_polling" {
  for_each         = { for lambda_key, lambda_conf in try(local.config.lambda, {}) : lambda_key => lambda_conf if lambda_conf.role == "dataProcessor" }
  event_source_arn = data.aws_sqs_queue.sqs_queue.arn
  function_name    = aws_lambda_function.lambda[each.key].arn
  enabled          = true
  batch_size       = 10
}
