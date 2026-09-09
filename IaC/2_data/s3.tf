module "s3_bucket" {
  for_each = {
    for s3_key, s3 in try(local.config.s3, {}) : "${local.identifier}-${s3.name}" => s3
  }
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.15.1"

  bucket           = each.value.bucket_namespace == "account-regional" ? "${each.key}-${data.aws_caller_identity.caller_identity.account_id}-${local.config.region}-an" : each.key
  bucket_namespace = each.value.bucket_namespace
  force_destroy    = true
  cors_rule = each.value.presigned_url == true ? [
    {
      allowed_headers = ["*"]
      allowed_methods = ["PUT", "POST"]
      allowed_origins = ["*"]
      expose_headers  = ["ETag"]
      max_age_seconds = 3000
    }
  ] : []
  versioning = {
    status = "Enabled"
  }
  object_ownership = each.value.website_bucket == true ? "BucketOwnerEnforced" : "ObjectWriter"

}

resource "aws_s3_object" "website_files" {
  for_each     = try(local.s3_website_files, {})
  bucket       = module.s3_bucket[each.value.s3Key].s3_bucket_id
  key          = each.value.file
  source       = "${path.module}/s3-website-utils/${each.value.file}"
  etag         = filemd5("${path.module}/s3-website-utils/${each.value.file}")
  content_type = lookup(local.s3_website_content_types, regex("\\.[^.]+$", each.value.file), "application/octet-stream")
}

resource "aws_s3_bucket_website_configuration" "website" {
  for_each = {
    for s3_key, s3 in try(local.config.s3, {}) : "${local.identifier}-${s3.name}" => s3
    if s3.website_bucket == true
  }
  bucket = module.s3_bucket[each.key].s3_bucket_id
  index_document {
    suffix = "index.html"
  }
  error_document {
    key = "error.html"
  }
}
