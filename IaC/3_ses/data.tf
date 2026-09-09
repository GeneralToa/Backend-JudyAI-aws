data "aws_route53_zone" "hosted_zone" {
  name         = local.config.ses.hostedZone
  private_zone = local.config.ses.privateZone
}

data "aws_cloudwatch_event_bus" "default" {
  name = "default"
}
