from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_site_contains_current_pricing_and_legal_links():
    page = (ROOT / "app/static/public_site.html").read_text(encoding="utf-8")
    for text in (
        "Тариф MaxiBot — 1 990 рублей в месяц",
        "Хрипунов Александр Сергеевич",
        "ИНН 505006274804",
        'href="/documents/oferta"',
        'href="/documents/politika-konfidencialnosti"',
        'href="/documents/politika-vozvrata"',
    ):
        assert text in page


def test_public_site_does_not_change_tracking_host_or_routes():
    caddy = (ROOT / "deploy/Caddyfile").read_text(encoding="utf-8")
    assert "go.maxibot.pro" in caddy
    assert "maxibot.pro, www.maxibot.pro" in caddy
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert '@app.get("/mini"' in main
    assert '@app.get("/go/{public_id}"' in main


def test_public_site_has_mobile_layout_layer_without_replacing_reference_images():
    page = (ROOT / "app/static/public_site.html").read_text(encoding="utf-8")
    mobile = (ROOT / "app/static/public_site_mobile.css").read_text(encoding="utf-8")
    assert '/static/public_site_mobile.css' in page
    assert '@media (max-width: 700px)' in mobile
    assert 'position: sticky' in mobile
    assert 'min-height: 40px' in mobile
    assert '.reference-section img' in mobile
    assert 'reference-hero.png' in page
    assert 'reference-how-it-works.png' in page
    assert 'reference-pricing.png' in page
    assert 'reference-try-free.png' in page
