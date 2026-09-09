resource "aws_route53_record" "cloudfront" {
  for_each = { for website in try(local.config.websites, {}) : website.name => website if website.route53.enabled }

  zone_id = data.aws_route53_zone.hosted_zone[each.key].zone_id
  name    = can(each.value.route53.subdomain) ? "${each.value.route53.subdomain}.${each.value.route53.hostedZone}" : each.value.route53.hostedZone
  type    = "A"

  alias {
    name                   = module.cloudfront[each.key].cloudfront_distribution_domain_name
    zone_id                = module.cloudfront[each.key].cloudfront_distribution_hosted_zone_id
    evaluate_target_health = false
  }
}
