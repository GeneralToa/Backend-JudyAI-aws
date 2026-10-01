locals {
  config        = yamldecode(file("${path.module}/config/${terraform.workspace}.yaml"))
  identifier    = "${local.config.identifier}-${terraform.workspace}"
  alarm_actions = local.config.sns.emailNotificationsEnabled ? [aws_sns_topic.alarms.arn] : []
}