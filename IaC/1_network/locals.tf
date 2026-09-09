locals {
  config     = yamldecode(file("${path.module}/config/${terraform.workspace}.yaml"))
  identifier = "${local.config.identifier}-${terraform.workspace}"
  vpc_endpoints = {
    for endpoint in flatten([
      for endpoint_key, endpoint_conf in try(local.config.vpcEndpoints, {}) : {
        key                 = endpoint_key
        service_name        = "com.amazonaws.${local.config.region}.${endpoint_conf.service}"
        service_type        = try(endpoint_conf.service_type, "Interface")
        route_table_ids     = try(endpoint_conf.service_type, "Interface") == "Gateway" ? module.vpc.private_route_table_ids : []
        subnets_ids         = try(endpoint_conf.service_type, "Interface") == "Interface" ? module.vpc.private_subnets : []
        private_dns_enabled = try(endpoint_conf.service_type, "Interface") == "Interface" ? endpoint_conf.private_dns_enabled : false
      }
      if endpoint_conf.enabled
    ]) : endpoint.key => endpoint
  }
}