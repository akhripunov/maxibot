from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_passkey_auth_uses_standard_verification_and_strict_origin():
    source = (ROOT / "app/web_admin_auth.py").read_text(encoding="utf-8")
    assert "verify_registration_response" in source
    assert "verify_authentication_response" in source
    assert "expected_origin=settings.web_admin_origin" in source
    assert "require_user_verification=True" in source


def test_passkey_session_is_opaque_and_secure():
    source = (ROOT / "app/web_admin_auth.py").read_text(encoding="utf-8")
    assert 'httponly=True' in source
    assert 'samesite="strict"' in source
    assert "token_hash=_digest(raw_session)" in source


def test_passkey_admin_has_server_session_logout_path():
    source = (ROOT / "app/web_admin_auth.py").read_text(encoding="utf-8")
    html = (ROOT / "app/static/admin/index.html").read_text(encoding="utf-8")
    js = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert '@router.post("/logout"' in source
    assert 'id="logout"' in html
    assert "/auth/logout" in js
    assert "revoked_at" in source


def test_frontend_uses_post_for_login_options_and_verify():
    js = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert "fetch(`${api}/auth/login/options`, {method: 'POST'" in js
    assert "fetch(`${api}/auth/login/verify`, {method: 'POST'" in js
    assert "fetch(`${api}/auth/register/verify`, {method: 'POST'" in js
    assert "getJson('/auth/login/options')" not in js


def test_frontend_clears_protected_view_on_auth_loss_and_reports_login_failure():
    js = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert "function resetLockedView()" in js
    assert "resetLockedView();" in js
    assert "Вход не выполнен:" in js
    assert "Выход не выполнен:" in js
    assert "Данные не загружены" in js


def test_frontend_auth_controls_have_one_render_authority():
    js = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert "function renderAuthState" in js
    for control in ("authBanner", "authActions", "bootstrapToken", "registerButton", "loginButton", "logoutButton"):
        assert js.count(f"{control}.hidden =") == 1


def test_admin_css_forces_hidden_elements_out_of_layout():
    css = (ROOT / "app/static/admin/admin.css").read_text(encoding="utf-8")
    assert ".app-shell [hidden]{display:none !important}" in css


def test_caddy_does_not_use_basic_auth_and_browser_never_gets_hop_token():
    caddy = (ROOT / "deploy/Caddyfile").read_text(encoding="utf-8").lower()
    js = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8").lower()
    assert "basic_auth" not in caddy
    assert "x-maxibot-admin-token" not in js
    assert "x-webadmin-bootstrap" in js
