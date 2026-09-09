resource "aws_sesv2_email_identity" "ses_email_identity" {
  email_identity = can(local.config.ses.subdomain) ? "${local.config.ses.subdomain}.${local.config.ses.hostedZone}" : "${local.config.ses.hostedZone}"
}

resource "aws_sesv2_configuration_set" "configuration" {
  for_each               = try(local.config.ses.configurationSet, {})
  configuration_set_name = "${local.identifier}-${each.value.name}"

  sending_options {
    sending_enabled = each.value.sendingEnabled
  }
}

resource "aws_sesv2_configuration_set_event_destination" "eventbridge" {
  for_each               = try(local.config.ses.configurationSet, {})
  configuration_set_name = aws_sesv2_configuration_set.configuration[each.key].configuration_set_name
  event_destination_name = "eventbridge"
  event_destination {
    event_bridge_destination {
      event_bus_arn = data.aws_cloudwatch_event_bus.default.arn
    }

    enabled = true
    matching_event_types = [
      "SEND",
      "DELIVERY",
      "BOUNCE",
      "COMPLAINT",
      "REJECT",
      "RENDERING_FAILURE"
    ]
  }
}

resource "aws_ses_template" "ses_template" {
  for_each = try(local.config.ses.templates, {})
  name     = each.value.name
  subject  = each.value.subject
  html     = templatefile("${path.module}/${each.value.htmlPath}", {})
}
