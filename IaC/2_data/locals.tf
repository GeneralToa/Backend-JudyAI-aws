locals {
  config     = yamldecode(file("${path.module}/config/${terraform.workspace}.yaml"))
  identifier = "${local.config.identifier}-${terraform.workspace}"
  #proxy_subnets_ids is created due to a known issue with RDS Proxy, which is incompatible with the use1-az3 Availability Zone.
  #Ref: https://github.com/hashicorp/terraform-provider-aws/issues/17781
  proxy_subnet_ids = [
    for id, subnet in data.aws_subnet.private_subnets_details : id
    if subnet.availability_zone_id != "use1-az3"
  ]
  s3_website_content_types = {
    ".html" = "text/html"
    ".css"  = "text/css"
    ".js"   = "application/javascript"
    ".json" = "application/json"
    ".png"  = "image/png"
    ".jpg"  = "image/jpeg"
    ".svg"  = "image/svg+xml"
    ".ico"  = "image/x-icon"
  }
  s3_website_files = merge([
    for file in fileset("${path.module}/s3-website-utils", "**") : {
      for s3_key, s3 in try(local.config.s3, {}) : "${local.identifier}-${s3.name}-${file}" => {
        s3Key = "${local.identifier}-${s3.name}"
        file  = file
      }
      if s3.websiteBucket == true
    }
    if file != "config.js"
  ]...)
}
