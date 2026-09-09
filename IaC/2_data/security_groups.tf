resource "aws_security_group" "aurora_postgres_sg" {
  description = "default VPC security group for Aurora PostgreSQL"
  name        = "${local.identifier}-aurora-postgres-sg"
  vpc_id      = data.aws_vpc.vpc.id
}

resource "aws_security_group" "lambda_sg" {
  description = "Security group for Lambda functions to RDS Proxy"
  name        = "${local.identifier}-lambda-sg"
  vpc_id      = data.aws_vpc.vpc.id
}

resource "aws_security_group" "ecs_sg" {
  description = "Security group for ECS"
  name        = "${local.identifier}-ecs-sg"
  vpc_id      = data.aws_vpc.vpc.id
}

resource "aws_security_group" "rds_proxy_sg" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  description = "Security group for RDS Proxy to Aurora PostgreSQL"
  name        = "${local.identifier}-${each.value.db_prefix}-proxy-sg"
  vpc_id      = data.aws_vpc.vpc.id
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_proxy" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  security_group_id            = aws_security_group.lambda_sg.id
  referenced_security_group_id = aws_security_group.rds_proxy_sg[each.key].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "ecs_egress" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
  }
  security_group_id            = aws_security_group.ecs_sg.id
  referenced_security_group_id = try(each.value.proxy_enabled, false) ? aws_security_group.rds_proxy_sg[each.key].id : aws_security_group.aurora_postgres_sg.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "proxy_from_lambda" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  security_group_id            = aws_security_group.rds_proxy_sg[each.key].id
  referenced_security_group_id = aws_security_group.lambda_sg.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "proxy_from_vpc" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  security_group_id = aws_security_group.rds_proxy_sg[each.key].id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "proxy_to_aurora" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  security_group_id            = aws_security_group.rds_proxy_sg[each.key].id
  referenced_security_group_id = aws_security_group.aurora_postgres_sg.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}


resource "aws_vpc_security_group_ingress_rule" "aurora_from_proxy" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == true
  }
  security_group_id            = aws_security_group.aurora_postgres_sg.id
  referenced_security_group_id = aws_security_group.rds_proxy_sg[each.key].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "aurora_from_ecs" {
  for_each = {
    for rdsAuroraKey, rdsAurora in try(local.config.rdsAurora) : rdsAuroraKey => rdsAurora
    if rdsAurora.proxy_enabled == false
  }
  security_group_id            = aws_security_group.aurora_postgres_sg.id
  referenced_security_group_id = aws_security_group.ecs_sg.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
