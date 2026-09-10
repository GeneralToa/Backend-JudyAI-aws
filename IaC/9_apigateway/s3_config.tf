resource "local_file" "config_js" {
  filename = "${path.module}/s3-website-utils/config.js"
  content  = <<-EOF
const CONFIG = {
    API_BASE_URL: "${module.api_gateway["lambdaGateway"].api_endpoint}",

    // Cognito settings
    COGNITO_DOMAIN: "${data.aws_cognito_user_pool.cognito["lambdaGateway"].domain}.auth.${local.config.region}.amazoncognito.com",
    COGNITO_CLIENT_ID: "${data.aws_cognito_user_pool_clients.cognito_clients["lambdaGateway"].client_ids[0]}",
    COGNITO_REDIRECT_URI: "https://${data.aws_ssm_parameter.cloudfront_domain_name.value}/callback.html",
    COGNITO_LOGOUT_URI: "https://${data.aws_ssm_parameter.cloudfront_domain_name.value}/index.html",
};
EOF
}

resource "aws_s3_object" "config_js" {
  bucket       = data.aws_s3_bucket.s3_bucket.id
  key          = "config.js"
  content      = local_file.config_js.content
  content_type = "application/javascript"
}
