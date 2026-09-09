resource "aws_service_discovery_http_namespace" "ecs" {
  for_each = toset([
    for service in try(local.config.ecs.services, {}) : service.namespace
  ])
  name = each.key
}

module "ecs" {
  source  = "terraform-aws-modules/ecs/aws"
  version = "7.6.0"

  cluster_name = "${local.identifier}-apps-cluster"

  cluster_configuration = {
    execute_command_configuration = {
      logging = "OVERRIDE"
      log_configuration = {
        cloud_watch_log_group_name = "/aws/ecs/${local.identifier}-apps-cluster"
      }
    }
  }

  # Cluster capacity providers
  cluster_capacity_providers = ["FARGATE"]
  # Base and Weight Rules: Like any strategy, it uses a base (the minimum number of tasks to run on a specific provider) 
  # and a weight (the proportional split for remaining tasks).
  default_capacity_provider_strategy = {
    FARGATE = {
      weight = 100
      base   = 1
    }
  }

  services = {
    for service_key, service in try(local.config.ecs.services, {}) : service_key => {
      name                               = "${service.name}-service"
      family                             = service.name
      cpu                                = service.cpu
      memory                             = service.memory
      enable_ecs_managed_tags            = false
      enable_execute_command             = true
      deployment_maximum_percent         = 200
      deployment_minimum_healthy_percent = 100
      force_new_deployment               = false
      assign_public_ip                   = false

      deployment_circuit_breaker = {
        enable   = true
        rollback = true
      }

      runtime_platform = {
        cpu_architecture        = "X86_64" #"ARM64"
        operating_system_family = "LINUX"
      }

      service_connect_configuration = {
        namespace = aws_service_discovery_http_namespace.ecs[service.namespace].arn
        service = try(service.serviceConnect, false) ? [{
          port_name      = "${service.name}-${service.hostPort}-${service.protocol}"
          discovery_name = service.name
          client_alias = {
            port     = service.containerPort
            dns_name = service.name
          }
        }] : []
      }

      health_check_grace_period_seconds = try(service.healthCheckGracePeriod, 60)

      container_definitions = {
        (service.name) = {
          name                   = service.name
          cpu                    = service.cpu
          memory                 = service.memory
          essential              = try(service.essential, false)
          image                  = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${local.config.region}.amazonaws.com/${local.identifier}-${service.repoName}:${service.imageTag}"
          readonlyRootFilesystem = false
          portMappings = [
            {
              name          = "${service.name}-${service.hostPort}-${service.protocol}"
              containerPort = tonumber(service.containerPort)
              hostPort      = tonumber(service.hostPort)
              protocol      = service.protocol
              appProtocol   = service.appProtocol
            }
          ]
          environment = can(service.environmentVariables) ? concat(
            [
              for cache_key, cache in try(local.cacheVariables, {}) : {
                name  = "REDIS_URL"
                value = "rediss://${data.aws_elasticache_replication_group.elasticache[cache_key].primary_endpoint_address}:6379"
              }
              if service_key == cache.serviceKey
            ],
            [
              for config_key, config in try(local.configVariables, {}) : {
                name  = config.name
                value = config.value
              }
              if service_key == config.serviceKey
            ]
          ) : []
          secrets = can(service.environmentVariables) ? concat(
            flatten([
              for secret_key, secret in try(local.dbVariables, {}) : [
                {
                  name      = "DATABASE_URL"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_rds[secret_key].secret_arn}:writer_jdbc_url::"
                },
                {
                  name      = "DATABASE_READER_URL"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_rds[secret_key].secret_arn}:reader_jdbc_url::"
                }
              ]
              if service_key == secret.serviceKey
            ]),
            flatten([
              for secret_key, secret in try(local.cognitoVariables, {}) : [
                {
                  name      = "COGNITO_USER_POOL_ID"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:user_pool_id::"
                },
                {
                  name      = "COGNITO_CLIENT_ID"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:client_id::"
                },
                {
                  name      = "COGNITO_CLIENT_SECRET"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:client_secret::"
                },
                {
                  name      = "COGNITO_REDIRECT_URI"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:callback_url::"
                },
                {
                  name      = "COGNITO_SCOPES"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:scopes::"
                },
                {
                  name      = "COGNITO_DOMAIN"
                  valueFrom = "${data.aws_secretsmanager_secret_version.secret_version_cognito[secret_key].secret_arn}:domain::"
                },
                {
                  name      = "APP_TOKEN"
                  valueFrom = aws_ssm_parameter.secret.arn
                }
              ]
              if service_key == secret.serviceKey
            ])
          ) : []
          cloudwatch_log_group_name              = "/${local.identifier}/ecs/${service.name}"
          cloudwatch_log_group_retention_in_days = 30
          cloudwatch_log_group_use_name_prefix   = false
        }
      }

      subnet_ids            = data.aws_subnets.private_subnets.ids
      create_security_group = false
      security_group_ids    = [data.aws_security_group.ecs_sg.id]

      load_balancer = {
        service = {
          target_group_arn = module.alb.target_groups[service_key].arn
          container_name   = service.name
          container_port   = service.containerPort
        }
      }

      create_task_exec_iam_role = false
      task_exec_iam_role_arn    = aws_iam_role.ecs_task_execution_role.arn
      create_tasks_iam_role     = false
      tasks_iam_role_arn        = aws_iam_role.ecs_task_role.arn

      autoscaling_policies = {
        ("${service.name}-scale-policy") = {
          policy_type = "TargetTrackingScaling"

          target_tracking_scaling_policy_configuration = {
            predefined_metric_specification = {
              predefined_metric_type = "ECSServiceAverageCPUUtilization"
            }
            scale_in_cooldown  = 60
            scale_out_cooldown = 60
            target_value       = try(service.cpu_target, 80)
          }
        }
      }
      autoscaling_max_capacity = try(service.autoScaling.maxCapacity, 3)
      autoscaling_min_capacity = try(service.autoScaling.minCapacity, 1)
      desired_count            = try(service.autoScaling.desiredCount, 0)
    }
  }
}
