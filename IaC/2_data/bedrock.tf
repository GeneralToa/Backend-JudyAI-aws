# --- Aurora schema setup for Bedrock KB ---
resource "null_resource" "bedrock_kb_schema" {
  triggers = {
    cluster_arn = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "CREATE EXTENSION IF NOT EXISTS vector" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "CREATE SCHEMA IF NOT EXISTS bedrock_integration" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$BEDROCK_USERNAME') THEN EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '$BEDROCK_USERNAME', '$BEDROCK_PASSWORD'); END IF; END \$\$;" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "DO \$\$ BEGIN EXECUTE format('GRANT ALL ON SCHEMA bedrock_integration TO %I', '$BEDROCK_USERNAME'); END \$\$;" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), embedding vector(1024), chunks text, metadata json, custom_metadata jsonb)" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "DO \$\$ BEGIN EXECUTE format('GRANT ALL ON TABLE bedrock_integration.bedrock_kb TO %I', '$BEDROCK_USERNAME'); END \$\$;" && \
      aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx ON bedrock_integration.bedrock_kb USING hnsw (embedding vector_cosine_ops)" && \
        aws rds-data execute-statement \
        --region ${local.config.region} \
        --resource-arn ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn} \
        --secret-arn ${aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn} \
        --database ${module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name} \
        --sql "CREATE INDEX IF NOT EXISTS bedrock_kb_chunks_idx ON bedrock_integration.bedrock_kb USING gin (to_tsvector('simple', chunks))"
    EOT

    environment = {
      BEDROCK_USERNAME = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_master_username
      BEDROCK_PASSWORD = random_password.aurora_random_password[local.config.bedrockKnowledgeBase.auroraDbKey].result
    }
  }

  depends_on = [
    module.aurora,
    aws_secretsmanager_secret_version.aurora_secret_version
  ]
}

# --- Bedrock Knowledge Base ---
resource "aws_bedrockagent_knowledge_base" "main" {
  name     = "${local.identifier}-${local.config.bedrockKnowledgeBase.name}"
  region   = try(local.config.bedrockKnowledgeBase.region, local.config.region)
  role_arn = aws_iam_role.bedrock_kb.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${local.config.region}::foundation-model/${local.config.bedrockKnowledgeBase.bedrock_embedding_model_id}"
    }
  }

  storage_configuration {
    type = "RDS"

    rds_configuration {
      credentials_secret_arn = aws_secretsmanager_secret.aurora_secret[local.config.bedrockKnowledgeBase.auroraDbKey].arn
      database_name          = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_database_name
      resource_arn           = module.aurora[local.config.bedrockKnowledgeBase.auroraDbKey].cluster_arn
      table_name             = "bedrock_integration.bedrock_kb"

      field_mapping {
        primary_key_field = "id"
        vector_field      = "embedding"
        text_field        = "chunks"
        metadata_field    = "metadata"
      }
    }
  }

  tags = {
    Name = "${local.identifier}-${local.config.bedrockKnowledgeBase.name}"
  }

  depends_on = [null_resource.bedrock_kb_schema]
}

# --- S3 Data Source for Knowledge Base (with BDA multimodal parsing) ---
resource "aws_bedrockagent_data_source" "s3" {
  name              = "${local.identifier}-s3-data-source"
  knowledge_base_id = aws_bedrockagent_knowledge_base.main.id

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn = module.s3_bucket["${local.identifier}-${local.config.bedrockKnowledgeBase.s3DataSource}"].s3_bucket_arn
    }
  }

  vector_ingestion_configuration {
    parsing_configuration {
      parsing_strategy = "BEDROCK_DATA_AUTOMATION"
    }
  }

  data_deletion_policy = "RETAIN"
}
