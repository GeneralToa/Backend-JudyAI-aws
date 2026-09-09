module "sqs_with_dlq" {
  for_each = try(local.config.sqs, {})
  source   = "terraform-aws-modules/sqs/aws"
  version  = "5.2.2"

  # This creates both the queue and the dead letter queue together

  name = "${local.identifier}-${each.value.name}-sqs"

  create_queue_policy = each.value.create_queue_policy
  queue_policy_statements = {
    account = {
      sid = "AccountReadWrite"
      actions = [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
      ]
      principals = [
        {
          type        = "AWS"
          identifiers = ["*"]
        }
      ]
    }
  }
  visibility_timeout_seconds = each.value.visibility_timeout_seconds
  message_retention_seconds  = each.value.message_retention_seconds
  receive_wait_time_seconds  = try(each.value.receive_wait_time_seconds, 0)

  # Dead letter queue
  create_dlq              = can(each.value.dlq) ? true : false
  dlq_name                = "${local.identifier}-${each.value.name}-sqs-dlq"
  create_dlq_queue_policy = each.value.dlq.create_queue_policy
  dlq_queue_policy_statements = {
    account = {
      sid = "AccountReadWrite"
      actions = [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
      ]
      principals = [
        {
          type        = "AWS"
          identifiers = ["*"]
        }
      ]
    }
  }
  redrive_policy = {
    # default is 5 for this module
    maxReceiveCount = each.value.dlq.max_receive_count
  }
  dlq_visibility_timeout_seconds  = each.value.dlq.visibility_timeout_seconds
  dlq_message_retention_seconds   = each.value.dlq.message_retention_seconds
  create_dlq_redrive_allow_policy = each.value.dlq.create_redrive_allow_policy
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  for_each = {
    for sqs_key, sqs_conf in try(local.config.sqs, {}) : sqs_key => sqs_conf
    if try(sqs_conf.bucketEvent.enabled, false)
  }

  bucket = module.s3_bucket["${local.identifier}-${each.value.bucketEvent.bucketName}"].s3_bucket_id

  queue {
    queue_arn     = module.sqs_with_dlq[each.key].queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = try(each.value.bucketEvent.filterPrefix, "")
  }
}
