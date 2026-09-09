module "endpoints" {
  source = "terraform-aws-modules/vpc/aws//modules/vpc-endpoints"

  vpc_id = module.vpc.vpc_id

  create_security_group = false
  security_group_ids    = [aws_security_group.vpc_endpoints_sg.id]

  endpoints = local.vpc_endpoints
}