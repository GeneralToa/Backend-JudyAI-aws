# ================ DynamoDB ================
module "dynamodb_table" {
  for_each = {
    for dynamoTable in try(local.config.dynamoDB, []) : dynamoTable.table_name => dynamoTable
  }
  source  = "terraform-aws-modules/dynamodb-table/aws"
  version = "5.5.0"

  name      = "${local.identifier}-${each.key}"
  hash_key  = each.value.hash_key
  range_key = try(each.value.range_key, null)

  server_side_encryption_enabled     = true
  server_side_encryption_kms_key_arn = aws_kms_key.dynamodb.arn

  attributes = each.value.attributes
}

resource "aws_dynamodb_contributor_insights" "table_insights" {
  for_each = {
    for dynamoTable in try(local.config.dynamoDB, []) : dynamoTable.table_name => dynamoTable
    if dynamoTable.table_insights
  }
  table_name = each.key
}

# ================= Aurora =================
resource "random_password" "aurora_random_password" {
  for_each         = try(local.config.rdsAurora, {})
  length           = 40
  special          = false
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

module "aurora" {
  for_each = try(local.config.rdsAurora, {})
  source   = "terraform-aws-modules/rds-aurora/aws"
  version  = "10.3.0"

  name                       = "${local.identifier}-${each.value.db_prefix}"
  engine                     = each.value.engine
  engine_mode                = each.value.engine_mode
  engine_version             = each.value.engine_version
  cluster_instance_class     = each.value.instance_class
  storage_encrypted          = true
  apply_immediately          = try(each.value.apply_immediately, false)
  auto_minor_version_upgrade = try(each.value.auto_minor_version_upgrade, false)
  region                     = try(each.value.region, local.config.region)
  kms_key_id                 = aws_kms_key.rdskey_aurora[each.key].arn

  is_primary_cluster          = each.value.is_primary_cluster
  database_name               = each.value.db_name
  port                        = each.value.port
  master_username             = each.value.username
  master_password_wo          = random_password.aurora_random_password[each.key].result
  master_password_wo_version  = 1
  manage_master_user_password = false

  preferred_maintenance_window    = each.value.maintenance_window
  preferred_backup_window         = each.value.backup_window
  backup_retention_period         = try(each.value.backup_retention_period, 7)
  skip_final_snapshot             = try(each.value.skip_final_snapshot, false)
  enabled_cloudwatch_logs_exports = each.value.cloudwatch_logs

  vpc_id                 = data.aws_vpc.vpc.id
  create_db_subnet_group = false
  db_subnet_group_name   = data.aws_db_subnet_group.db_subnet_group.name
  subnets                = data.aws_subnets.database_subnets.ids
  vpc_security_group_ids = [aws_security_group.aurora_postgres_sg.id]

  cluster_monitoring_interval = 60
  monitoring_role_arn         = aws_iam_role.aurora_monitoring_role.arn
  enable_http_endpoint        = true

  #Vertical Serverless Scaling
  serverlessv2_scaling_configuration = each.value.engine_mode == "provisioned" ? {
    min_capacity = each.value.serverless_scaling_configuration.min_capacity
    max_capacity = each.value.serverless_scaling_configuration.max_capacity
  } : {}

  #Horizontal Readers Scaling
  autoscaling_enabled      = each.value.autoscaling_configuration.enabled
  autoscaling_min_capacity = each.value.autoscaling_configuration.min_capacity
  autoscaling_max_capacity = each.value.autoscaling_configuration.max_capacity

  cluster_timeouts = {
    delete = "30m"
  }

  instances = {
    one = {}
  }
  instance_timeouts = {
    delete = "30m"
  }
}

module "rds_proxy" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }

  source  = "terraform-aws-modules/rds-proxy/aws"
  version = "4.4.0"

  name                   = "${local.identifier}-${each.value.db_prefix}-proxy"
  region                 = try(each.value.region, local.config.region)
  iam_role_name          = "${local.identifier}-${each.value.db_prefix}-role"
  vpc_subnet_ids         = local.proxy_subnet_ids
  vpc_security_group_ids = [aws_security_group.rds_proxy_sg[each.key].id]
  kms_key_arns           = [aws_kms_key.rdskey_aurora[each.key].arn]

  endpoints = {
    read_write = {
      name                   = "read-write-endpoint"
      vpc_subnet_ids         = local.proxy_subnet_ids
      vpc_security_group_ids = [aws_security_group.rds_proxy_sg[each.key].id]
    },
    read_only = {
      name                   = "read-only-endpoint"
      vpc_subnet_ids         = local.proxy_subnet_ids
      vpc_security_group_ids = [aws_security_group.rds_proxy_sg[each.key].id]
      target_role            = "READ_ONLY"
    }
  }

  auth = {
    "${each.value.username}" = {
      description               = "Aurora PostgreSQL superuser password"
      secret_arn                = aws_secretsmanager_secret.aurora_secret[each.key].arn
      auth_scheme               = "SECRETS"
      iam_auth                  = "DISABLED"
      client_password_auth_type = "POSTGRES_SCRAM_SHA_256"
    }
  }

  # Target Aurora cluster
  engine_family         = "POSTGRESQL"
  target_db_cluster     = true
  db_cluster_identifier = module.aurora[each.key].cluster_id
}
