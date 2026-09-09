data "aws_caller_identity" "current" {}

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

data "aws_subnets" "public_subnets" {
  filter {
    name   = "tag:Env"
    values = [terraform.workspace]
  }

  filter {
    name   = "tag:layer"
    values = ["public"]
  }

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.vpc.id]
  }
}

data "aws_subnet" "subnet_elb" {
  for_each = local.config.loadBalancer.internal ? toset(data.aws_subnets.private_subnets.ids) : toset(data.aws_subnets.public_subnets.ids)
  id       = each.value
  region   = local.config.region
}

data "aws_security_group" "ecs_sg" {
  name   = "${local.identifier}-ecs-sg"
  vpc_id = data.aws_vpc.vpc.id
}

data "aws_elasticache_replication_group" "elasticache" {
  for_each             = try(local.cacheVariables, {})
  replication_group_id = each.value.replicationGroupId
}

data "aws_secretsmanager_secret" "secret_manager_rds" {
  for_each = try(local.dbVariables, {})
  name     = "/rds/${each.value.dbSecretName}/aurora-password"
}

data "aws_secretsmanager_secret_version" "secret_version_rds" {
  for_each  = try(local.dbVariables, {})
  secret_id = data.aws_secretsmanager_secret.secret_manager_rds[each.key].id
}


data "aws_acm_certificate" "acm_certificate" {
  domain = "*.${local.config.loadBalancer.tlsDomain}"
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

data "aws_secretsmanager_secret" "secret_manager_cognito" {
  for_each = try(local.cognitoVariables, {})
  name     = "/cognito/${each.value.userPoolClient}/pool-data"
}

data "aws_secretsmanager_secret_version" "secret_version_cognito" {
  for_each  = try(local.cognitoVariables, {})
  secret_id = data.aws_secretsmanager_secret.secret_manager_cognito[each.key].id
}

data "aws_s3_bucket" "s3_bucket" {
  for_each = try(local.config.s3, {})
  bucket   = "${local.identifier}-${each.value.name}"
}
