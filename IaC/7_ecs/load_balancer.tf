module "alb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "~> 10.0"

  name = "${local.config.loadBalancer.name}-${local.identifier}"

  load_balancer_type = local.config.loadBalancer.type
  internal           = local.config.loadBalancer.internal

  vpc_id  = data.aws_vpc.vpc.id
  subnets = local.elb_subnets_ids

  # For example only
  enable_deletion_protection = false

  create_security_group = false
  security_groups       = [aws_security_group.elb_sg.id]

  listeners = {
    http-https-redirect = {
      port     = 80
      protocol = "HTTP"
      redirect = {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
    https = {
      port            = 443
      protocol        = "HTTPS"
      certificate_arn = data.aws_acm_certificate.acm_certificate.arn

      fixed_response = {
        content_type = "text/plain"
        status_code  = "404"
        message_body = "404: service not found"
      }

      rules = {
        for service_key, service in try(local.config.ecs.services, {}) : service_key => {
          priority = service.elbPriority
          actions = [{
            forward = {
              target_group_key = service_key
            }
          }]
          conditions = [{
            path_pattern = {
              values = [service.elbPath]
            }
          }]
        }
      }
    }
  }

  target_groups = {
    for service_key, service in try(local.config.ecs.services, {}) : service_key => {
      protocol    = "HTTP"
      port        = service.containerPort
      target_type = "ip"

      health_check = {
        enabled             = true
        healthy_threshold   = 2
        interval            = 30
        matcher             = "200"
        path                = service.healthCheckPath
        port                = "traffic-port"
        protocol            = "HTTP"
        timeout             = 15
        unhealthy_threshold = 5
      }

      # There's nothing to attach here in this definition. Instead,
      # ECS will attach the IPs of the tasks to this target group
      create_attachment = false
    }
  }
}
