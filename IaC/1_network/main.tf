module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.6.1"

  name = local.identifier
  cidr = local.config.cidr

  azs                          = slice(data.aws_availability_zones.available.names, 0, local.config.azs)
  private_subnets              = local.config.private_subnet_cidrs
  public_subnets               = local.config.public_subnet_cidrs
  database_subnets             = local.config.data_subnet_cidrs
  create_database_subnet_group = try(local.config.create_database_subnet_group, true)
  one_nat_gateway_per_az       = false
  single_nat_gateway           = true
  map_public_ip_on_launch      = true

  enable_nat_gateway               = try(local.config.enable_nat_gateway, true)
  enable_vpn_gateway               = false
  create_private_nat_gateway_route = true

  public_subnet_tags = {
    "layer" = "public"
  }

  private_subnet_tags = {
    "layer" = "private"
  }

  database_subnet_tags = {
    "layer" = "data"
  }
}