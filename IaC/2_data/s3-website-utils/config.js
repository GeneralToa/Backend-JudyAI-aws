// =============================================================
// Configuration - Update these values after Terraform deployment
// =============================================================
const CONFIG = {
    // API Gateway invoke URL (e.g., https://xxxxxx.execute-api.us-east-1.amazonaws.com/v1)
    API_BASE_URL: "YOUR_API_GATEWAY_URL",

    // Cognito settings
    COGNITO_DOMAIN: "YOUR_COGNITO_DOMAIN.auth.us-east-1.amazoncognito.com",
    COGNITO_CLIENT_ID: "YOUR_COGNITO_APP_CLIENT_ID",
    COGNITO_REDIRECT_URI: "YOUR_CLOUDFRONT_URL/callback.html",
    COGNITO_LOGOUT_URI: "YOUR_CLOUDFRONT_URL/index.html",
};
