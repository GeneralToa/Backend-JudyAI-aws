resource "aws_ssm_parameter" "cloudfront_domain_name" {
  for_each = try(local.config.websites, {})
  name     = "/${local.identifier}/cloudfront-${each.value.name}/domain-name"
  type     = "String"
  value    = module.cloudfront[each.key].cloudfront_distribution_domain_name

  tags = {
    Name = "${local.identifier}-cf-${each.value.name}-domain-name"
  }
}
