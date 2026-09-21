// =============================================================
// Authentication via Cognito Hosted UI (OAuth2 Authorization Code flow)
// =============================================================

function getIdToken() {
    return sessionStorage.getItem("id_token");
}

function isAuthenticated() {
    return !!getIdToken();
}

function redirectToLogin() {
    const loginUrl =
        `https://${CONFIG.COGNITO_DOMAIN}/login?` +
        `client_id=${CONFIG.COGNITO_CLIENT_ID}` +
        `&response_type=token` +
        `&scope=email+openid+profile` +
        `&redirect_uri=${encodeURIComponent(CONFIG.COGNITO_REDIRECT_URI)}`;
    window.location.href = loginUrl;
}

function logout() {
    sessionStorage.clear();
    const logoutUrl =
        `https://${CONFIG.COGNITO_DOMAIN}/logout?` +
        `client_id=${CONFIG.COGNITO_CLIENT_ID}` +
        `&logout_uri=${encodeURIComponent(CONFIG.COGNITO_LOGOUT_URI)}`;
    window.location.href = logoutUrl;
}

function parseTokenFromHash() {
    const hash = window.location.hash.substring(1);
    const params = new URLSearchParams(hash);
    const token = params.get("id_token");
    if (token) {
        sessionStorage.setItem("id_token", token);
        window.location.hash = "";
    }
}

// On page load: parse token from hash or check auth
parseTokenFromHash();
if (!isAuthenticated() && !window.location.pathname.includes("callback")) {
    redirectToLogin();
}
