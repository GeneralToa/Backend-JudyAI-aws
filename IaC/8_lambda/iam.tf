# ================= DATA UPLOAD POLICY ===================
resource "aws_iam_policy" "data_upload_policy" {
  name = "${local.identifier}-data-upload-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${data.aws_s3_bucket.s3_bucket.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = [data.aws_dynamodb_table.dynamodb_table.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [
          data.aws_ssm_parameter.kms_dynamodb_arn.value,
          data.aws_ssm_parameter.kms_aurora_postgres_arn.value
        ]
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameters"]
        Resource = [
          data.aws_ssm_parameter.aurora_postgres_arn.arn,
          data.aws_ssm_parameter.kms_aurora_postgres_arn.arn,
          data.aws_ssm_parameter.app_writer_secret_arn.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.app_writer_secret_arn.value
      }
    ]
  })
}

# ============== DATA PROCESSOR POLICY ================
resource "aws_iam_policy" "data_processor_policy" {
  name = "${local.identifier}-data-processor-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:StartIngestionJob", "bedrock:ListIngestionJobs", "bedrock:GetIngestionJob"]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:knowledge-base/*"
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameters"]
        Resource = [
          data.aws_ssm_parameter.knowledge_base_id.arn,
          data.aws_ssm_parameter.data_source_id.arn,
          data.aws_ssm_parameter.kms_aurora_postgres_arn.arn,
          data.aws_ssm_parameter.aurora_postgres_arn.arn,
          data.aws_ssm_parameter.app_writer_secret_arn.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [data.aws_sqs_queue.sqs_queue["dataProcessor"].arn]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:DeleteItem",
          "dynamodb:UpdateItem",
          "dynamodb:Scan"
        ]
        Resource = [data.aws_dynamodb_table.dynamodb_table.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:DeleteObject",
          "s3:GetObject"
        ]
        Resource = "${data.aws_s3_bucket.s3_bucket.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule"
        ]
        Resource = "arn:aws:scheduler:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:schedule/default/${local.identifier}-monitor-*"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.scheduler_execution.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [
          data.aws_ssm_parameter.kms_dynamodb_arn.value,
          data.aws_ssm_parameter.kms_aurora_postgres_arn.value
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.app_writer_secret_arn.value
      }
    ]
  })
}

# =============== RETRIEVE AND GENERATE POLICY =================
resource "aws_iam_policy" "rag_policy" {
  name = "${local.identifier}-rag-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:RetrieveAndGenerate",
          "bedrock:Retrieve"
        ]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:knowledge-base/*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:GetInferenceProfile"
        ]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:inference-profile/${local.config.bedrock.inferenceProfileId}"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = data.aws_ssm_parameter.kms_aurora_postgres_arn.value
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:RollbackTransaction"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.judy_ai_writer_secret_arn.value
      }
    ]
  })
}

# =============== INGESTION POST PROCESSOR POLICY =================
resource "aws_iam_policy" "ingestion_post_processor_policy" {
  name = "${local.identifier}-ingestion-post-processor-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:ListIngestionJobs", "bedrock:GetIngestionJob", "bedrock:StartIngestionJob"]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:knowledge-base/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [data.aws_ssm_parameter.knowledge_base_id.arn, data.aws_ssm_parameter.data_source_id.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [data.aws_dynamodb_table.dynamodb_table.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule",
          "scheduler:DeleteSchedule"
        ]
        Resource = "arn:aws:scheduler:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:schedule/default/${local.identifier}-monitor-*"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.scheduler_execution.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = data.aws_ssm_parameter.kms_dynamodb_arn.value
      }
    ]
  })
}

# =============== SCHEDULER EXECUTION POLICY =================
resource "aws_iam_policy" "scheduler_execution_policy" {
  for_each = { for lambda_key, lambda_conf in try(local.config.lambda, {}) : lambda_key => lambda_conf if lambda_conf.role == "ingestionPostProcessor" }
  name     = "${local.identifier}-scheduler-execution-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.lambda[each.key].arn]
    }]
  })
}

# =============== DOCUMENT EXTRACTION POLICY =================
resource "aws_iam_policy" "document_extraction_policy" {
  name = "${local.identifier}-document-extraction-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${data.aws_s3_bucket.s3_bucket.arn}/*"
      },
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeDataAutomationAsync"]
        Resource = [
          "arn:aws:bedrock:*:${data.aws_caller_identity.caller_identity.account_id}:data-automation-project/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.caller_identity.account_id}:data-automation-profile/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:GetDataAutomationStatus"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = data.aws_ssm_parameter.kms_aurora_postgres_arn.value
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.judy_ai_writer_secret_arn.value
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          for lambda_key, lambda in try(local.config.lambda, {}) :
          "arn:aws:lambda:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:function:${local.identifier}-${lambda.functionName}"
          if contains(["agentRiskClause", "agentTemplatePrepopulation"], lambda.role)
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [data.aws_sqs_queue.sqs_queue["documentExtraction"].arn]
      },
    ]
  })
}

# =============== AGENT RISK CLAUSE POLICY =================
resource "aws_iam_policy" "agent_risk_clause_policy" {
  name = "${local.identifier}-agent-risk-clause-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:GetInferenceProfile"
        ]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:inference-profile/${local.config.bedrock.inferenceProfileId}"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = data.aws_ssm_parameter.kms_aurora_postgres_arn.value
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.judy_ai_writer_secret_arn.value
      }
    ]
  })
}

# =============== AGENT TEMPLATE PREPOPULATION POLICY =================
resource "aws_iam_policy" "agent_template_prepopulation_policy" {
  name = "${local.identifier}-agent-template-prepopulation-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${data.aws_s3_bucket.s3_bucket.arn}/bda-output/*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:GetInferenceProfile"
        ]
        Resource = "arn:aws:bedrock:${local.config.region}:${data.aws_caller_identity.caller_identity.account_id}:inference-profile/${local.config.bedrock.inferenceProfileId}"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = data.aws_ssm_parameter.kms_aurora_postgres_arn.value
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.judy_ai_writer_secret_arn.value
      }
    ]
  })
}

# =============== SIGNATURE WORKFLOW POLICY =================
resource "aws_iam_policy" "signature_workflow_policy" {
  name = "${local.identifier}-signature-workflow-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [
          data.aws_ssm_parameter.kms_aurora_postgres_arn.value
        ]
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameters"]
        Resource = [
          data.aws_ssm_parameter.aurora_postgres_arn.arn,
          data.aws_ssm_parameter.kms_aurora_postgres_arn.arn,
          data.aws_ssm_parameter.app_writer_secret_arn.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement"
        ]
        Resource = data.aws_ssm_parameter.aurora_postgres_arn.value
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_ssm_parameter.app_writer_secret_arn.value
      }
    ]
  })
}

resource "aws_iam_role" "scheduler_execution" {
  name = "${local.identifier}-scheduler-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role" "lambda_role" {
  for_each = try(local.config.lambda, {})
  name     = "${local.identifier}-${each.value.role}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["sts:AssumeRole"]
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_execution_role_att" {
  for_each   = { for lambda_key, lambda_conf in try(local.config.lambda, {}) : lambda_key => lambda_conf if lambda_conf.role == "ingestionPostProcessor" }
  role       = aws_iam_role.scheduler_execution.name
  policy_arn = aws_iam_policy.scheduler_execution_policy[each.key].arn
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  for_each   = try(local.config.lambda, {})
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_data_upload_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, {}) : lambda_key => lambda_conf
    if lambda_conf.role == "dataUpload"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.data_upload_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_data_processor_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "dataProcessor"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.data_processor_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_rag_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "retrieveAndGenerate"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.rag_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_ingestion_post_processor_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "ingestionPostProcessor"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.ingestion_post_processor_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_document_extraction_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "documentExtraction"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.document_extraction_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_agent_risk_clause_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "agentRiskClause"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.agent_risk_clause_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_agent_template_prepopulation_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "agentTemplatePrepopulation"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.agent_template_prepopulation_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_signature_workflow_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.role == "signatureWorkflow"
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = aws_iam_policy.signature_workflow_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_vpc_access_role_att" {
  for_each = {
    for lambda_key, lambda_conf in try(local.config.lambda, []) : lambda_key => lambda_conf
    if lambda_conf.vpcConfig
  }
  role       = aws_iam_role.lambda_role[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

