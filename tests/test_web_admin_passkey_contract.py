from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.web_admin_security import (
    enforce_web_admin_rate_limit,
    is_owner_console_path,
    owner_console_security_headers,
    require_owner_console_host,
    require_owner_console_origin,
)


ROOT = Path(__file__).resolve().parents[1]


def _request(host="admin.maxibot.pro", *, origin=None, forwarded_host=None):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if forwarded_host is not None:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    return Request({
        "type": "http", "method": "POST", "scheme": "https",
        "server": (host.split(":")[0], 443), "client": ("192.0.2.10", 1234),
        "path": "/api/admin/auth/login/options", "headers": headers,
    })


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


def test_admin_browser_boundary_requires_exact_host_and_origin_not_forwarded_headers():
    require_owner_console_host(_request())
    require_owner_console_origin(_request(origin="https://admin.maxibot.pro"))
    for request in (
        _request(host="api.maxibot.pro", origin="https://admin.maxibot.pro", forwarded_host="admin.maxibot.pro"),
        _request(origin="https://evil.example"),
        _request(origin=None),
    ):
        with pytest.raises(HTTPException) as error:
            require_owner_console_origin(request)
        assert error.value.status_code in {403, 404}
    assert is_owner_console_path("/admin")
    assert is_owner_console_path("/static/admin/index.html")
    assert is_owner_console_path("/api/admin/auth/login/options")
    assert not is_owner_console_path("/api/public")


def test_admin_response_headers_keep_console_private_and_unframed():
    headers = owner_console_security_headers()
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "object-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=")


def test_owner_console_routes_are_hidden_off_domain_and_emit_headers_on_admin_domain(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.config.settings.uploads_dir", str(tmp_path / "uploads"))
    from app.main import app

    client = TestClient(app)
    blocked = client.get("/static/admin/index.html", headers={"host": "api.maxibot.pro", "x-forwarded-host": "admin.maxibot.pro"})
    assert blocked.status_code == 404

    page = client.get("/static/admin/index.html", headers={"host": "admin.maxibot.pro"})
    assert page.status_code == 200
    assert page.headers["content-security-policy"].find("frame-ancestors 'none'") >= 0
    assert page.headers["strict-transport-security"].startswith("max-age=")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"

    owner_root = client.get("/", headers={"host": "admin.maxibot.pro"})
    public_root = client.get("/", headers={"host": "api.maxibot.pro"})
    assert owner_root.status_code == 200 and "OWNER CONSOLE" in owner_root.text
    assert owner_root.headers["content-security-policy"]
    assert "OWNER CONSOLE" not in public_root.text

    rejected = client.post(
        "/api/admin/auth/login/options",
        headers={"host": "api.maxibot.pro", "origin": "https://admin.maxibot.pro", "x-maxibot-admin-token": "forged"},
    )
    assert rejected.status_code == 404

    wrong_origin = client.post(
        "/api/admin/auth/login/options",
        headers={"host": "admin.maxibot.pro", "origin": "https://evil.example"},
    )
    assert wrong_origin.status_code == 403


@pytest.mark.asyncio
async def test_admin_auth_rate_limit_uses_socket_peer_and_fails_closed(monkeypatch):
    import app.web_admin_security as security

    calls = []

    class FakeRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        async def eval(self, _script, _key_count, key, period):
            calls.append((key, period))
            return 1

        async def aclose(self):
            return None

    monkeypatch.setattr(security, "Redis", FakeRedis)
    await enforce_web_admin_rate_limit(_request(forwarded_host="forged.example"), "login-verify")
    assert calls == [("web-admin:login-verify:6d99cbd08fc6c99cdb2d942a4cbb097c6b54496bbbc3ffd6351b145508dd2935", 60)]

    class FailingRedis(FakeRedis):
        async def eval(self, *_args):
            raise OSError("redis unavailable")

    monkeypatch.setattr(security, "Redis", FailingRedis)
    with pytest.raises(HTTPException) as error:
        await enforce_web_admin_rate_limit(_request(), "login-verify")
    assert error.value.status_code == 503
