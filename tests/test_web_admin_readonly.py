import pytest
from fastapi import HTTPException

from app.web_admin_auth import require_edge_token, require_qa_ingest_token
from app.web_admin_readonly import canonical_metric_definitions


def test_metric_definitions_mark_unsupported_business_metrics_unknown():
    definitions = canonical_metric_definitions()
    assert definitions["mrr"]["status"] == "UNKNOWN"
    assert definitions["cancellations"]["status"] == "UNKNOWN"
    assert definitions["conversion"]["status"] == "UNKNOWN"


def test_metric_definitions_are_explicit_about_paid_and_active_sources():
    definitions = canonical_metric_definitions()
    assert "SubscriptionOrder" in definitions["paying_clients"]["definition"]
    assert "subscription_expires_at" in definitions["active_subscriptions"]["definition"]


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
