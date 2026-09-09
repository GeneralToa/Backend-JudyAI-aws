# ===================== ECS =====================
resource "aws_vpc_security_group_ingress_rule" "allow_vpc_to_containers" {
  for_each          = try(local.config.ecs.services, {})
  security_group_id = data.aws_security_group.ecs_sg.id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = each.value.hostPort
  to_port           = each.value.hostPort
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "allow_containers_to_redis" {
  security_group_id = data.aws_security_group.ecs_sg.id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = 6379
  to_port           = 6379
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_ecr_to_containers" {
  security_group_id = data.aws_security_group.ecs_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "allow_containers_to_ecr" {
  security_group_id = data.aws_security_group.ecs_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# ===================== ALB =====================
resource "aws_security_group" "elb_sg" {
  description = "Security group for ELB"
  name        = "${local.config.loadBalancer.name}-${local.identifier}-sg"
  vpc_id      = data.aws_vpc.vpc.id
}

resource "aws_vpc_security_group_ingress_rule" "allow_http_from_vpc" {
  count             = local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_https_from_vpc" {
  count             = local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_https_from_cloudfront" {
  count             = local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "private_alb_egress_to_targets" {
  count             = local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = data.aws_vpc.vpc.cidr_block
  from_port         = 0
  to_port           = 65535
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_http_from_internet" {
  count             = !local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_https_from_internet" {
  count             = !local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "allow_https_from_cloudfront_public_alb" {
  count             = !local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "public_alb_egress_to_targets" {
  count             = !local.config.loadBalancer.internal ? 1 : 0
  security_group_id = aws_security_group.elb_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 0
  to_port           = 65535
  ip_protocol       = "tcp"
}

