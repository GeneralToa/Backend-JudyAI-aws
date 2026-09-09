# ===================== Aurora Role =====================
resource "aws_iam_role" "aurora_monitoring_role" {
  assume_role_policy = <<POLICY
{
  "Statement": [
    {
      "Action": "sts:AssumeRole",
      "Effect": "Allow",
      "Principal": {
        "Service": "monitoring.rds.amazonaws.com"
      },
      "Sid": ""
    }
  ],
  "Version": "2012-10-17"
}
POLICY

  max_session_duration = "3600"
  name                 = "${local.identifier}-aurora-monitoring-role"
  path                 = "/"
}

resource "aws_iam_role_policy_attachment" "aurora_monitoring_role_enhanced_monitoring" {
  role       = aws_iam_role.aurora_monitoring_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ===================== Bedrock Knowledge Base Role =====================
resource "aws_iam_role" "bedrock_kb" {
  name = "${local.identifier}-bedrock-kb-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.caller_identity.account_id
        }
      }
    }]
  })

  tags = {
    Name = "${local.identifier}-bedrock-kb-role"
  }
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "${local.identifier}-bedrock-kb-policy"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          module.s3_bucket["${local.identifier}-${local.config.bedrockKnowledgeBase.s3DataSource}"].s3_bucket_arn,
          "${module.s3_bucket["${local.identifier}-${local.config.bedrockKnowledgeBase.s3DataSource}"].s3_bucket_arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = ["arn:aws:bedrock:${local.config.region}::foundation-model/${local.config.bedrockKnowledgeBase.bedrock_embedding_model_id}"]
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement",
          "rds-data:BatchExecuteStatement"
        ]
        Resource = [module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn]
      },
      {
        Effect = "Allow"
        Action = [
          "rds:DescribeDBClusters"
        ]
        Resource = [module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = [aws_kms_key.rdskey_aurora[local.config.bedrockKnowledgeBase.auroraDbKey].arn]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:StartIngestionJob",
          "bedrock:GetIngestionJob",
          "bedrock:ListIngestionJobs"
        ]
        Resource = "*"
      },
      # Required for BEDROCK_DATA_AUTOMATION parsing strategy
      # BDA may route requests across regions, so we use wildcards
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeDataAutomationAsync",
          "bedrock:GetDataAutomationStatus"
        ]
        Resource = [
          "arn:aws:bedrock:*:aws:data-automation-project/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.caller_identity.account_id}:data-automation-profile/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.caller_identity.account_id}:data-automation-invocation/*"
        ]
      }
    ]
  })
}
