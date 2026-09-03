from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_site_contains_robokassa_required_information():
    page = (ROOT / "app/static/public_site.html").read_text(encoding="utf-8")
    for text in (
        "Доступ к МАКСИБОТ — 30 дней",
        "1 900 ₽",
        "Хрипунов Александр Сергеевич",
        "505006274804",
        "Щёлково",
        "akhripunov@bk.ru",
        "Отказ от услуги и возврат денежных средств",
        "Политика обработки персональных данных",
        "Комиссия Robokassa или банка из суммы возврата не удерживается.",
    ):
        assert text in page


def test_public_site_does_not_change_tracking_host_or_routes():
    caddy = (ROOT / "deploy/Caddyfile").read_text(encoding="utf-8")
    assert "go.maxibot.pro" in caddy
    assert "maxibot.pro, www.maxibot.pro" in caddy
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert '@app.get("/mini"' in main
    assert '@app.get("/go/{public_id}"' in main
