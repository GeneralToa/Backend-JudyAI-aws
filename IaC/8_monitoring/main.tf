# ================= SNS TOPIC (alarm notifications) =================
resource "aws_sns_topic" "alarms" {
  name = "${local.identifier}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  for_each = {
    for email in try(local.config.sns.alarmEmails, []) : email => email
    if local.config.sns.emailNotificationsEnabled
  }
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.key
}

# ================= LAMBDA =================
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = try(local.config.lambda, {})

  alarm_name          = "${local.identifier}-lambda-${each.value.functionName}-errors"
  alarm_description   = "Lambda ${each.value.functionName} error rate is too high"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = "${local.identifier}-${each.value.functionName}" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# Occurs when incoming requests exceed the concurrent execution limits (default: 1,000 concurrent executions per region). 
# This can happen if the function is invoked too frequently or if it takes too long to complete.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = try(local.config.lambda, {})

  alarm_name          = "${local.identifier}-lambda-${each.value.functionName}-throttles"
  alarm_description   = "Lambda ${each.value.functionName} is being throttled"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = "${local.identifier}-${each.value.functionName}" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 25
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  for_each = try(local.config.lambda, {})

  alarm_name          = "${local.identifier}-lambda-${each.value.functionName}-duration"
  alarm_description   = "Lambda ${each.value.functionName} p95 duration is approaching timeout"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = "${local.identifier}-${each.value.functionName}" }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = each.value.p95TimeoutThreshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ================= API GATEWAY =================
resource "aws_cloudwatch_metric_alarm" "apigw_5xx" {
  for_each = try(local.config.apiGateway, {})

  alarm_name          = "${local.identifier}-apigw-${each.value.name}-5xx"
  alarm_description   = "API Gateway ${local.identifier}-${each.value.name} 5XX error rate is too high"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  dimensions          = { ApiId = data.aws_ssm_parameter.apigateway_id.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "apigw_4xx" {
  for_each = try(local.config.apiGateway, {})

  alarm_name          = "${local.identifier}-apigw-${each.key}-4xx"
  alarm_description   = "API Gateway ${local.identifier}-${each.value.name} 4XX error rate is too high"
  namespace           = "AWS/ApiGateway"
  metric_name         = "4XXError"
  dimensions          = { ApiId = data.aws_ssm_parameter.apigateway_id.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 50
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "apigw_latency" {
  for_each = try(local.config.apiGateway, {})

  alarm_name          = "${local.identifier}-apigw-${each.key}-latency"
  alarm_description   = "API Gateway ${local.identifier}-${each.value.name} p95 latency is too high"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  dimensions          = { ApiId = data.aws_ssm_parameter.apigateway_id.value }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = 5000
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ================= SNS =================
resource "aws_cloudwatch_metric_alarm" "sns_notifications_failed" {
  for_each = try(local.config.snsTopics, {})

  alarm_name          = "${local.identifier}-sns-${each.value.name}-notifications-failed"
  alarm_description   = "SNS topic ${each.value.name} failed to deliver to a subscriber — documents may be silently lost"
  namespace           = "AWS/SNS"
  metric_name         = "NumberOfNotificationsFailed"
  dimensions          = { TopicName = "${local.identifier}-${each.value.name}" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ================= SQS =================
resource "aws_cloudwatch_metric_alarm" "sqs_message_age" {
  for_each = try(local.config.sqs, {})

  alarm_name          = "${local.identifier}-${each.value.name}-message-age"
  alarm_description   = "SQS queue ${local.identifier}-${each.value.name} has stale messages — consumer lag detected"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = "${local.identifier}-${each.value.name}" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 300
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "sqs_dlq_depth" {
  for_each = try(local.config.sqs, {})

  alarm_name          = "${local.identifier}-${each.value.dlq}-depth"
  alarm_description   = "SQS DLQ ${local.identifier}-${each.value.dlq} has messages — processing failures detected"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = "${local.identifier}-${each.value.dlq}" }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ================= AURORA =================
resource "aws_cloudwatch_metric_alarm" "aurora_cpu" {
  for_each = try(local.config.rdsAurora, {})

  alarm_name          = "${local.identifier}-${each.value.db_prefix}-cpu"
  alarm_description   = "Aurora cluster ${local.identifier}-${each.value.db_prefix} CPU utilization is too high"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBClusterIdentifier = "${local.identifier}-${each.value.db_prefix}" }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "aurora_connections" {
  for_each = try(local.config.rdsAurora, {})

  alarm_name          = "${local.identifier}-${each.value.db_prefix}-connections"
  alarm_description   = "Aurora cluster ${local.identifier}-${each.value.db_prefix} has too many database connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBClusterIdentifier = "${local.identifier}-${each.value.db_prefix}" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 800
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "aurora_capacity" {
  for_each = try(local.config.rdsAurora, {})

  alarm_name          = "${local.identifier}-${each.value.db_prefix}-capacity"
  alarm_description   = "Aurora Serverless v2 ${local.identifier}-${each.value.db_prefix} is approaching max ACU capacity"
  namespace           = "AWS/RDS"
  metric_name         = "ServerlessDatabaseCapacity"
  dimensions          = { DBClusterIdentifier = "${local.identifier}-${each.value.db_prefix}" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 4
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# Aurora Replica Lag alarm is commented out because no reader instances are being created in the RDS module. If you want to enable this alarm, you need to create at least one reader instance in the RDS module.
# resource "aws_cloudwatch_metric_alarm" "aurora_replica_lag" {
#   for_each = try(local.config.rdsAurora, {})

#   alarm_name          = "${local.identifier}-${each.value.db_prefix}-replica-lag"
#   alarm_description   = "Aurora cluster ${local.identifier}-${each.value.db_prefix} replica lag is too high"
#   namespace           = "AWS/RDS"
#   metric_name         = "AuroraReplicaLag"
#   dimensions          = { DBClusterIdentifier = "${local.identifier}-${each.value.db_prefix}" }
#   statistic           = "Maximum"
#   period              = 60
#   evaluation_periods  = 3
#   threshold           = 5000
#   comparison_operator = "GreaterThanOrEqualToThreshold"
#   treat_missing_data  = "notBreaching"
#   alarm_actions       = local.alarm_actions
#   ok_actions          = local.alarm_actions
# }

# ================= DYNAMODB =================
resource "aws_cloudwatch_metric_alarm" "dynamodb_throttles" {
  for_each = {
    for dynamoTable in try(local.config.dynamoDB, []) : dynamoTable.table_name => dynamoTable
  }
  alarm_name          = "${local.identifier}-dynamodb-${each.key}-throttles"
  alarm_description   = "DynamoDB table ${local.identifier}-${each.key} is being throttled"
  namespace           = "AWS/DynamoDB"
  metric_name         = "ThrottledRequests"
  dimensions          = { TableName = "${local.identifier}-${each.key}" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_system_errors" {
  for_each = {
    for dynamoTable in try(local.config.dynamoDB, []) : dynamoTable.table_name => dynamoTable
  }
  alarm_name          = "${local.identifier}-dynamodb-${each.key}-system-errors"
  alarm_description   = "DynamoDB table ${local.identifier}-${each.key} has internal system errors"
  namespace           = "AWS/DynamoDB"
  metric_name         = "SystemErrors"
  dimensions          = { TableName = "${local.identifier}-${each.key}" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ================= SES =================
resource "aws_cloudwatch_metric_alarm" "ses_bounce_rate" {
  alarm_name          = "${local.identifier}-ses-bounce-rate"
  alarm_description   = "SES bounce rate is approaching the 5% AWS warning threshold"
  namespace           = "AWS/SES"
  metric_name         = "Reputation.BounceRate"
  statistic           = "Average"
  period              = 900
  evaluation_periods  = 2
  threshold           = 0.04
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "ses_complaint_rate" {
  alarm_name          = "${local.identifier}-ses-complaint-rate"
  alarm_description   = "SES complaint rate is approaching the 0.1% AWS warning threshold"
  namespace           = "AWS/SES"
  metric_name         = "Reputation.ComplaintRate"
  statistic           = "Average"
  period              = 900
  evaluation_periods  = 2
  threshold           = 0.0009
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}
