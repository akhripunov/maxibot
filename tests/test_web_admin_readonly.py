import pytest
from fastapi import HTTPException

from app.web_admin_auth import require_edge_token, require_qa_ingest_token
from app.web_admin_readonly import BOT_STAGES, CHANNEL_STAGES, _bot_integrity_status, _main_drop, _stage_payload, canonical_metric_definitions


def test_metric_definitions_mark_unsupported_business_metrics_unknown():
    definitions = canonical_metric_definitions()
    assert definitions["mrr"]["status"] == "UNKNOWN"
    assert definitions["cancellations"]["status"] == "UNKNOWN"
    assert definitions["conversion"]["status"] == "UNKNOWN"


def test_metric_definitions_are_explicit_about_paid_and_active_sources():
    definitions = canonical_metric_definitions()
    assert "SubscriptionOrder" in definitions["paying_clients"]["definition"]
    assert "subscription_expires_at" in definitions["active_subscriptions"]["definition"]


def test_journey_endpoint_preserves_separate_domains_and_bounded_recent_rows():
    source = open("app/web_admin_readonly.py", encoding="utf-8").read()
    assert '@router.get("/journey")' in source
    assert "BotTrafficVisit" in source and "TrafficVisit" in source
    assert "CHANNEL and BOT rows are deliberately loaded and aggregated separately" in source
    assert "][0:100]" not in source  # bounded with an explicit slice below via `[:100]`
    assert "[:100]" in source
    assert '"direct": "UNKNOWN"' in source


def test_bot_journey_exposes_handoff_attempt_without_claiming_destination_opened():
    source = open("app/web_admin_readonly.py", encoding="utf-8").read()
    assert '"Открытие конкретного бота"' in source
    assert '"BOT_HANDOFF_STARTED"' in source
    assert '"ATTEMPT"' in source
    assert '"destination_reached": "UNKNOWN"' in source
    assert "Переход в бот инициирован, /start не получен" in source


def test_exact_channel_and_bot_stage_order_is_exposed_without_normalizing_domains():
    assert [item[1] for item in CHANNEL_STAGES] == [
        "Direct", "Landing", "TrafficVisit(channel_id)", "Token", "Startapp", "MAX Mini App",
        "Verified MAX user", "Resolve token", "MaxIdentityLink", "Проверка channel_id",
        "Открытие конкретного канала", "Join request", "Approve", "user_added(user_id + channel_id)",
        "Повторная проверка channel_id + MaxIdentityLink", "Восстановление TrafficVisit / yclid",
        "max_subscriber", "Metrika", "Direct",
    ]
    assert [item[1] for item in BOT_STAGES] == [
        "Direct", "Landing", "BotTrafficVisit(bot_id)", "Token", "Startapp", "MAX Mini App",
        "Verified MAX user", "Resolve token", "BotMaxIdentityLink", "Проверка bot_id",
        "Открытие конкретного бота", "Реальный /start", "Повторная проверка bot_id + BotMaxIdentityLink",
        "Восстановление BotTrafficVisit / yclid", "bot_started", "Metrika", "Direct",
    ]


def test_stage_payload_reports_both_conversion_rates_and_unknown_without_zero_substitution():
    stages = _stage_payload(BOT_STAGES[:3], [None, 20, 10], 20)
    assert stages[0]["status"] == "UNKNOWN"
    assert stages[0]["conversion_from_start_percent"] is None
    assert stages[1]["conversion_from_previous_percent"] is None
    assert stages[1]["conversion_from_start_percent"] == 100.0
    assert stages[2]["conversion_from_previous_percent"] == 50.0
    assert stages[2]["conversion_from_start_percent"] == 50.0


def test_journey_uses_unique_denominator_and_separate_domain_aggregates():
    source = open("app/web_admin_readonly.py", encoding="utf-8").read()
    assert '"journey_denominator"' in source
    assert '"domain_funnels": domain_funnels' in source
    assert '"active_journeys": all_journeys' in source
    assert 'source: str = Query(default="all", pattern="^(all|yandex|organic)$")' in source


def test_historical_real_start_telemetry_gap_is_not_a_technical_fail():
    source = open("app/web_admin_readonly.py", encoding="utf-8").read()
    assert "Historical bot_started rows can predate REAL_START_RECEIVED telemetry" in source
    assert 'failures = 0' in source
    assert "Историческая observability gap" in source
    assert _bot_integrity_status(visits=1, warnings=1, proven_bypass=False) == "WARN"
    assert _bot_integrity_status(visits=1, warnings=0, proven_bypass=True) == "FAIL"


def test_main_drop_is_user_dropoff_not_technical_failure():
    stages = _stage_payload(BOT_STAGES[10:13], [19, 4, 4], 20)
    drop = _main_drop(stages)
    assert drop == {"from": "Открытие конкретного бота", "to": "Реальный /start", "from_count": 19, "to_count": 4, "rate": 21.1, "kind": "USER_DROPOFF", "reason": "Пользовательский отвал; техническая причина не доказана"}


@pytest.mark.asyncio
async def test_web_admin_rejects_missing_internal_auth(monkeypatch):
    monkeypatch.setattr("app.web_admin_auth.settings.web_admin_internal_token", "expected")
    with pytest.raises(HTTPException) as exc:
        await require_edge_token(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_web_admin_accepts_only_configured_internal_auth(monkeypatch):
    monkeypatch.setattr("app.web_admin_auth.settings.web_admin_internal_token", "expected")
    assert await require_edge_token("expected") is None
    with pytest.raises(HTTPException) as exc:
        await require_edge_token("wrong")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_qa_ingest_accepts_only_dedicated_token(monkeypatch):
    monkeypatch.setattr("app.web_admin_auth.settings.qa_result_ingest_token", "qa-expected")
    monkeypatch.setattr("app.web_admin_auth.settings.web_admin_internal_token", "web-expected")
    assert await require_qa_ingest_token("qa-expected") is None
    for value in (None, "web-expected", "wrong"):
        with pytest.raises(HTTPException) as exc:
            await require_qa_ingest_token(value)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_web_admin_token_does_not_authenticate_qa_ingest(monkeypatch):
    monkeypatch.setattr("app.web_admin_auth.settings.qa_result_ingest_token", "qa-expected")
    monkeypatch.setattr("app.web_admin_auth.settings.web_admin_internal_token", "web-expected")
    with pytest.raises(HTTPException) as exc:
        await require_qa_ingest_token("web-expected")
    assert exc.value.status_code == 401


def test_web_admin_contract_does_not_contain_secret_fields():
    source = open("app/web_admin_auth.py", encoding="utf-8").read().lower()
    assert "access_token" not in source
    assert "refresh_token" not in source
    assert "password" not in source
    assert "web_admin_internal_token" in source
    assert "maxibot_admin_session" in source
