data "aws_caller_identity" "caller_identity" {}

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

data "aws_subnets" "database_subnets" {
  filter {
    name   = "tag:Env"
    values = [terraform.workspace]
  }

  filter {
    name   = "tag:layer"
    values = ["data"]
  }

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.vpc.id]
  }
}

data "aws_db_subnet_group" "db_subnet_group" {
  name = local.identifier
}

#is created due to a known issue with RDS Proxy, which is incompatible with the use1-az3 Availability Zone.
#Ref: https://github.com/hashicorp/terraform-provider-aws/issues/17781
data "aws_subnet" "private_subnets_details" {
  for_each = toset(data.aws_subnets.private_subnets.ids)
  id       = each.value
}
