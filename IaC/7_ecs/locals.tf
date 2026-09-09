locals {
  config     = yamldecode(file("${path.module}/config/${terraform.workspace}.yaml"))
  identifier = "${local.config.identifier}-${terraform.workspace}"
  elb_subnets_by_az = {
    for subnet in data.aws_subnet.subnet_elb : subnet.availability_zone => subnet.id...
  }
  elb_subnets_ids = [for az, ids in local.elb_subnets_by_az : ids[0]]
  dbVariables = merge([
    for service_key, service in try(local.config.ecs.services, {}) : {
      for env_var in try(service.environmentVariables, {}) : "${service_key}-${env_var.name}" => {
        serviceKey   = service_key
        dbSecretName = "${local.identifier}-${env_var.name}-rds"
      }
      if env_var.type == "DB"
    }
  ]...)
  cacheVariables = merge([
    for service_key, service in try(local.config.ecs.services, {}) : {
      for env_var in try(service.environmentVariables, {}) : "${service_key}-${env_var.name}" => {
        serviceKey         = service_key
        replicationGroupId = "${local.config.identifier}-${env_var.name}-${terraform.workspace}-${local.config.region}"
      }
      if env_var.type == "CACHE"
    }
  ]...)
  cognitoVariables = merge([
    for service_key, service in try(local.config.ecs.services, {}) : {
      for env_var in try(service.environmentVariables, {}) : "${service_key}-${env_var.name}" => {
        serviceKey     = service_key
        userPoolClient = "${local.identifier}-${env_var.name}-client"
      }
      if env_var.type == "COGNITO"
    }
  ]...)
  configVariables = merge([
    for service_key, service in try(local.config.ecs.services, {}) : {
      for env_var in try(service.environmentVariables, {}) : "${service_key}-${env_var.name}" => {
        serviceKey = service_key
        name       = env_var.name
        value      = env_var.value
      }
      if env_var.type == "CONFIG"
    }
  ]...)
}
