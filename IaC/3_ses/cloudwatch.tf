resource "aws_cloudwatch_log_group" "ses_logs" {
  name              = "/aws/ses/events"
  retention_in_days = 30
}

resource "aws_cloudwatch_event_rule" "ses_events" {
  name = "ses-events"
  event_pattern = jsonencode({
    source = [
      "aws.ses"
    ]
    detail-type = [
      "Email Bounced",
      "Email Delivered",
      "Email Sent",
      "Email Complaint"
    ]
  })
}

resource "aws_cloudwatch_event_target" "logs" {
  arn  = aws_cloudwatch_log_group.ses_logs.arn
  rule = aws_cloudwatch_event_rule.ses_events.name
}