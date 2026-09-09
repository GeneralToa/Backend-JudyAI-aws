resource "aws_route53_record" "ses_dkim" {
  count   = 3
  zone_id = data.aws_route53_zone.hosted_zone.zone_id
  name    = can(local.config.ses.subdomain) ? "${aws_sesv2_email_identity.ses_email_identity.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.config.ses.subdomain}.${local.config.ses.hostedZone}" : "${aws_sesv2_email_identity.ses_email_identity.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.config.ses.hostedZone}"
  type    = "CNAME"
  ttl     = 300
  records = ["${aws_sesv2_email_identity.ses_email_identity.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}
