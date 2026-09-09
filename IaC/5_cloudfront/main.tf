module "cloudfront_log_bucket" {
  count   = local.config.createLogBucket ? 1 : 0
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.15.1"

  bucket_prefix = "${local.identifier}-cloudfront-logs"

  control_object_ownership = true
  object_ownership         = "ObjectWriter"

  block_public_acls       = false
  block_public_policy     = true
  ignore_public_acls      = false
  restrict_public_buckets = true

  grant = [{
    type       = "CanonicalUser"
    permission = "FULL_CONTROL"
    id         = data.aws_canonical_user_id.current.id
    }, {
    type       = "CanonicalUser"
    permission = "FULL_CONTROL"
    id         = data.aws_cloudfront_log_delivery_canonical_user_id.cloudfront.id
  }]

  force_destroy = true
}

module "cloudfront" {
  for_each = try(local.config.websites, {})
  source   = "terraform-aws-modules/cloudfront/aws"
  version  = "6.7.0"

  aliases = each.value.tlsCertificate ? can(each.value.route53.subdomain) ? ["${each.value.route53.subdomain}.${each.value.route53.hostedZone}"] : ["${each.value.route53.hostedZone}"] : null

  comment             = "[${local.identifier}] CloudFront for ${each.value.origin.name}"
  wait_for_deployment = false
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = each.value.origin.type == "s3" ? "index.html" : null
  price_class         = "PriceClass_100"

  logging_config = local.config.createLogBucket ? {
    bucket = module.cloudfront_log_bucket[0].s3_bucket_bucket_domain_name
    prefix = each.value.name
  } : null

  origin_access_control = each.value.origin.type == "s3" ? {
    "s3-oac-${terraform.workspace}" = {
      description      = "CloudFront access to S3"
      origin_type      = "s3"
      signing_behavior = "always"
      signing_protocol = "sigv4"
    }
  } : null

  vpc_origin = each.value.origin.type == "alb" ? {
    "alb-vpc-oac-${terraform.workspace}" = {
      arn                    = data.aws_lb.alb[each.key].arn
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols = {
        items    = ["TLSv1.2"]
        quantity = 1
      }
    }
  } : null


  origin = {
    frontend = {
      domain_name               = each.value.origin.type == "s3" ? data.aws_s3_bucket.s3_bucket[each.key].bucket_regional_domain_name : data.aws_lb.alb[each.key].dns_name
      origin_access_control_key = each.value.origin.type == "s3" ? "s3-oac-${terraform.workspace}" : null
      vpc_origin_config = each.value.origin.type == "alb" ? {
        vpc_origin_key           = "alb-vpc-oac-${terraform.workspace}"
        origin_keepalive_timeout = 5
        origin_read_timeout      = 30
      } : null
    }
  }

  custom_error_response = each.value.origin.type == "s3" ? [
    {
      error_code         = 404
      response_code      = 200
      response_page_path = "/index.html"
    },
    {
      error_code         = 403
      response_code      = 200
      response_page_path = "/index.html"
    }
  ] : null

  default_cache_behavior = {
    target_origin_id         = "frontend"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = each.value.origin.type == "s3" ? ["GET", "HEAD", "OPTIONS"] : ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"                  #"658327ea-f89d-4fab-a63d-7e88639e58f6"                  #REF: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html
    origin_request_policy_id = each.value.origin.type == "alb" ? data.aws_cloudfront_origin_request_policy.all_viewer.id : null
    forwarded_values = {
      query_string = false
      cookies = {
        forward = "none"
      }
    }
  }

  viewer_certificate = each.value.tlsCertificate ? {
    acm_certificate_arn      = data.aws_acm_certificate.acm_certificate[each.key].arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
    } : {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }
}
