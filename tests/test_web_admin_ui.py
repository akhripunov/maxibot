from pathlib import Path

from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parents[1]


def test_admin_shell_is_separate_read_only_owner_dashboard():
    page = ROOT / "app/static/admin/index.html"
    text = page.read_text(encoding="utf-8")
    HTMLParser().feed(text)
    for label in ("Обзор", "Клиенты", "Подписки и оплаты", "Каналы", "AI Директолог", "ADS", "QA", "Релизы", "Система", "Хранилище"):
        assert label in text
    assert "/api/admin" in (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert "password" not in text.lower()


def test_admin_shell_has_no_fake_business_values_and_distinguishes_unknown():
    text = (ROOT / "app/static/admin/index.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    assert 'data-metric="mrr">N/A' in text
    assert "valueOrUnknown" in script
    assert "График появится после накопления данных" in text
    assert "fake" not in text.lower()


def test_admin_route_serves_shell_without_changing_api_namespace():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    routes = (ROOT / "app/api/routes.py").read_text(encoding="utf-8")
    assert '@app.get("/admin", response_class=FileResponse)' in source
    assert 'router = APIRouter(prefix="/admin"' in (ROOT / "app/web_admin_readonly.py").read_text(encoding="utf-8")
    assert 'router.include_router(web_admin_router)' in routes


def test_admin_domain_uses_root_as_canonical_entrypoint():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'request.url.hostname == "admin.maxibot.pro"' in source
    assert 'return RedirectResponse("/", status_code=308)' in source
    assert "public_site_file" in source
