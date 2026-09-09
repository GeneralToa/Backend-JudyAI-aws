resource "aws_cognito_user_pool" "pool" {
  name = "${local.identifier}-${local.config.userPool.name}"

  password_policy {
    minimum_length    = local.config.userPool.passwordPolicy.minimumLength
    require_lowercase = local.config.userPool.passwordPolicy.requireLowercase
    require_numbers   = local.config.userPool.passwordPolicy.requireNumbers
    require_symbols   = local.config.userPool.passwordPolicy.requireSymbols
    require_uppercase = local.config.userPool.passwordPolicy.requireUppercase
  }

  mfa_configuration = try(local.config.userPool.mfaEnabled, "OFF") ? "ON" : "OFF"
  dynamic "software_token_mfa_configuration" {
    for_each = local.config.userPool.mfaEnabled ? [1] : []
    content {
      enabled = true
    }
  }

  dynamic "admin_create_user_config" {
    for_each = local.config.userPool.adminCreateUserOnly ? [1] : []
    content {
      allow_admin_create_user_only = true
      invite_message_template {
        email_message = "Your username is {username} and temporary password is {####}."
        email_subject = "Your account has been created."
        sms_message   = "Your username is {username} and temporary password is {####}."
      }
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  username_attributes      = local.config.userPool.usernameAttributes
  auto_verified_attributes = ["email"]
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }
  email_configuration {
    email_sending_account = local.config.userPool.emailConfiguration.sendingAccount
    from_email_address    = local.config.userPool.emailConfiguration.sendingAccount == "DEVELOPER" ? "${local.config.userPool.emailConfiguration.emailConfiguration.fromAddress}@${local.config.userPool.emailConfiguration.emailConfiguration.domain}" : null
    source_arn            = local.config.userPool.emailConfiguration.sendingAccount == "DEVELOPER" ? data.aws_sesv2_email_identity.ses[0].arn : null
  }

  user_pool_add_ons {
    advanced_security_mode = "OFF"
  }

  dynamic "schema" {
    for_each = try(local.config.userPool.customStringAttributes, {})
    content {
      name                     = schema.value.name
      attribute_data_type      = schema.value.attributeDataType
      developer_only_attribute = schema.value.developerOnlyAttribute
      mutable                  = schema.value.mutable
      required                 = false
      string_attribute_constraints {
        min_length = schema.value.stringAttributeConstraints.minLength
        max_length = schema.value.stringAttributeConstraints.maxLength
      }
    }
  }

  dynamic "schema" {
    for_each = try(local.config.userPool.customNumberAttributes, {})
    content {
      name                     = schema.value.name
      attribute_data_type      = schema.value.attributeDataType
      developer_only_attribute = schema.value.developerOnlyAttribute
      mutable                  = schema.value.mutable
      required                 = false
      number_attribute_constraints {
        min_value = schema.value.numberAttributeConstraints.minValue
        max_value = schema.value.numberAttributeConstraints.maxValue
      }
    }
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain                = "${local.identifier}-${local.config.userPool.domain_prefix}"
  user_pool_id          = aws_cognito_user_pool.pool.id
  managed_login_version = local.config.userPool.managedLoginVersion
}

resource "aws_cognito_user_pool_client" "client" {
  for_each                      = try(local.config.userPool.userPoolClient, {})
  name                          = "${local.identifier}-${local.config.userPool.name}-client"
  user_pool_id                  = aws_cognito_user_pool.pool.id
  generate_secret               = false
  prevent_user_existence_errors = "ENABLED"
  supported_identity_providers  = ["COGNITO"]

  allowed_oauth_flows_user_pool_client = each.value.hostedUI.enabled
  callback_urls                        = each.value.hostedUI.enabled ? each.value.hostedUI.callback_urls : []
  logout_urls                          = each.value.hostedUI.enabled ? each.value.hostedUI.logout_urls : []
  allowed_oauth_scopes                 = each.value.hostedUI.enabled ? each.value.hostedUI.scopes : []
  allowed_oauth_flows                  = each.value.hostedUI.enabled ? each.value.hostedUI.flows : []

  explicit_auth_flows = each.value.explicitAuthFlows

  access_token_validity  = each.value.tokenValidityUnits.accessTokenValidity
  id_token_validity      = each.value.tokenValidityUnits.idTokenValidity
  refresh_token_validity = each.value.tokenValidityUnits.refreshTokenValidity

  token_validity_units {
    access_token  = each.value.tokenValidityUnits.accessTokenUnit
    id_token      = each.value.tokenValidityUnits.idTokenUnit
    refresh_token = each.value.tokenValidityUnits.refreshTokenUnit
  }
}

# --- Cognito Hosted UI Customization (Judy.ai branding) ---
resource "aws_cognito_user_pool_ui_customization" "ui_customization" {
  for_each     = try(local.config.userPool.userPoolClient, {})
  user_pool_id = aws_cognito_user_pool.pool.id
  client_id    = aws_cognito_user_pool_client.client[each.key].id

  css = file("${path.module}/login-style/cognito-custom.css")

  depends_on = [aws_cognito_user_pool_domain.this]
}


# ==================== NEW MANAGED LOGIN BRANDING RESOURCE ====================
# resource "aws_cognito_managed_login_branding" "managed_login" {
#   for_each     = try(local.config.userPool.userPoolClient, {})
#   client_id    = aws_cognito_user_pool_client.client[each.key].id
#   user_pool_id = aws_cognito_user_pool.pool.id

#   settings = file("${path.module}/login-style/settings.json")
#   dynamic "asset" {
#     for_each = jsondecode(file("${path.module}/login-style/assets.json"))
#     content {
#       category    = asset.value["Category"]
#       color_mode  = asset.value["ColorMode"]
#       extension   = asset.value["Extension"]
#       bytes       = asset.value["Bytes"]
#       resource_id = try(asset.value["ResourceId"], null)
#     }
#   }
# }
