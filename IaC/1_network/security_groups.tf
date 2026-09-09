resource "aws_security_group" "vpc_endpoints_sg" {
  description = "VPC security group for VPC Endpoints"

  ingress {
    from_port   = "443"
    protocol    = "tcp"
    self        = "false"
    to_port     = "443"
    cidr_blocks = module.vpc.private_subnets_cidr_blocks
  }

  name   = "${local.identifier}-vpc-endpoints-sg"
  vpc_id = module.vpc.vpc_id
}