terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.55.0"
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = "1.103.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = local.config.region
  assume_role {
    role_arn     = local.config.role_arn
    session_name = "terraform-judy"
  }
  default_tags {
    tags = merge({
      Workspace = terraform.workspace
      Env       = terraform.workspace
      Terraform = "true"
    }, local.config.tags)
  }
}


provider "awscc" {
  region = local.config.region
  assume_role = {
    role_arn     = local.config.role_arn
    session_name = "terraform-judy"
  }
}
