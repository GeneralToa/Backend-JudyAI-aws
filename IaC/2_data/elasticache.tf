# module "elasticache" {
#   source               = "terraform-aws-modules/elasticache/aws"
#   version              = "1.11.1"
#   replication_group_id = "${local.config.identifier}-${local.config.elasticache.name}-${terraform.workspace}-${local.config.region}"

#   engine                 = "valkey"
#   engine_version         = "9.0"
#   parameter_group_family = "valkey9"
#   node_type              = local.config.elasticache.node_type

#   transit_encryption_enabled = true
#   maintenance_window         = "sun:05:00-sun:09:00"
#   apply_immediately          = try(local.config.elasticache.apply_immediately, false)

#   # Security Group
#   vpc_id = data.aws_vpc.vpc.id
#   security_group_rules = {
#     ingress_vpc = {
#       # Default type is `ingress`
#       # Default port is based on the default engine port
#       description = "VPC traffic"
#       cidr_ipv4   = data.aws_vpc.vpc.cidr_block
#     }
#   }

#   # Subnet Group
#   subnet_group_name        = "${local.config.identifier}-${local.config.elasticache.name}-${terraform.workspace}-${local.config.region}-subnet"
#   subnet_group_description = "Redis replication group subnet group"
#   subnet_ids               = data.aws_subnets.private_subnets.ids

#   # Parameter Group
#   create_parameter_group      = true
#   parameter_group_name        = "${local.config.identifier}-${local.config.elasticache.name}-${terraform.workspace}-${local.config.region}-parameter"
#   parameter_group_description = "Redis replication group parameter group"
#   parameters = [
#     {
#       name  = "latency-tracking"
#       value = "yes"
#     }
#   ]

#   # Backups
#   snapshot_retention_limit = try(local.config.elasticache.snapshot_retention_limit, 1)
# }
