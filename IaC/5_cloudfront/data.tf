data "aws_canonical_user_id" "current" {}
data "aws_cloudfront_log_delivery_canonical_user_id" "cloudfront" {}
data "aws_caller_identity" "caller_identity" {}

data "aws_vpc" "vpc" {
  filter {
    name   = "tag:Env"
    values = [terraform.workspace]
  }
}

data "aws_acm_certificate" "acm_certificate" {
  for_each = {
    for website_key, website_conf in try(local.config.websites, {}) : website_key => website_conf
    if website_conf.tlsCertificate
  }
  provider = aws.us_east_1
  domain   = "*.${each.value.tlsDomain}"
}

data "aws_s3_bucket" "s3_bucket" {
  for_each = {
    for website_key, website in try(local.config.websites, {}) : website_key => website
    if website.origin.type == "s3"
  }
  bucket = try(each.value.origin.bucketNamespace, "") == "account-regional" ? "${local.identifier}-${each.value.origin.name}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : "${local.identifier}-${each.value.origin.name}"
}

data "aws_lb" "alb" {
  for_each = {
    for website_key, website in try(local.config.websites, {}) : website_key => website
    if website.origin.type == "alb"
  }
  name = "${each.value.origin.name}-${local.identifier}"
}

data "aws_route53_zone" "hosted_zone" {
  for_each     = { for website_key, website in try(local.config.websites, {}) : website_key => website if website.route53.enabled }
  name         = each.value.route53.hostedZone
  private_zone = false
}

data "aws_security_group" "elb_sg" {
  for_each = {
    for website_key, website in try(local.config.websites, {}) : website_key => website
    if website.origin.type == "alb"
  }
  name   = "${each.value.origin.name}-${local.identifier}-sg"
  vpc_id = data.aws_vpc.vpc.id
}

#Required to use https-only in origin_protocol_policy of the VPC Origin.
data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}
