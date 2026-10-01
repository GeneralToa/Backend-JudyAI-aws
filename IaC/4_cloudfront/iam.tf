resource "aws_s3_bucket_policy" "bucket_policy" {
  for_each = {
    for website_key, website in try(local.config.websites, {}) : website_key => website
    if website.origin.type == "s3"
  }
  bucket = data.aws_s3_bucket.s3_bucket[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "bucketContent"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action = [
          "s3:GetObject"
        ]
        Resource = ["${data.aws_s3_bucket.s3_bucket[each.key].arn}/*"]
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = module.cloudfront[each.key].cloudfront_distribution_arn
          }
        }
      }
    ]
  })
}
