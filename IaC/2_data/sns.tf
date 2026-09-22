module "sns_topic" {
  for_each = try(local.config.sns, {})
  source   = "terraform-aws-modules/sns/aws"
  version  = "7.1.1"

  name = "${local.identifier}-${each.value.name}-topic"

  topic_policy_statements = {
    pub = {
      actions = ["sns:Publish"]
      principals = [{
        type        = "AWS"
        identifiers = ["*"]
      }]
    },

    sub = {
      actions = [
        "sns:Subscribe",
        "sns:Receive",
      ]

      principals = [{
        type        = "AWS"
        identifiers = ["*"]
      }]

      condition = can(each.value.subscription) ? [{
        test     = "StringLike"
        variable = "sns:Endpoint"
        values = flatten([
          for sub_key, sub in each.value.subscription : [
            module.sqs_with_dlq[sub_key].queue_arn
          ]
          if sub.protocol == "sqs"
        ])
      }] : []
    }
  }

  subscriptions = can(each.value.subscription) ? {
    for sub_key, sub in each.value.subscription : sub_key => {
      protocol = sub.protocol
      endpoint = module.sqs_with_dlq[sub_key].queue_arn
    }
  } : {}
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  for_each = {
    for sns_key, sns_conf in try(local.config.sns, {}) : sns_key => sns_conf
    if try(sns_conf.bucketEvent.enabled, false)
  }

  bucket = module.s3_bucket["${local.identifier}-${each.value.bucketEvent.bucketName}"].s3_bucket_id

  topic {
    topic_arn     = module.sns_topic[each.key].topic_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = try(each.value.bucketEvent.filterPrefix, "")
  }
}
