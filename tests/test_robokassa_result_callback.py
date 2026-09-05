from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import Request

from app.core.config import settings
from app.integrations.robokassa import valid_result_signature


MAIN_SOURCE = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
CALLBACK_SOURCE = MAIN_SOURCE[MAIN_SOURCE.index("async def robokassa_result"):MAIN_SOURCE.index("@app.get(\"/payments/robokassa/success\", response_class=HTMLResponse)")]


def test_valid_result_signature_is_accepted_without_exposing_secret(monkeypatch):
    monkeypatch.setattr(settings, "robokassa_test_mode", True)
    monkeypatch.setattr(settings, "robokassa_test_password2", "isolated-test-password")
    from hashlib import md5

    signature = md5("1900.00:12345:isolated-test-password".encode()).hexdigest()
    assert valid_result_signature(amount="1900.00", invoice_id="12345", signature=signature)
    assert "isolated-test-password" not in CALLBACK_SOURCE


def test_invalid_result_signature_is_rejected():
    assert not valid_result_signature(amount="1900.00", invoice_id="12345", signature="invalid")


def test_callback_credits_only_pending_order_and_is_idempotent():
    assert 'if order.status == "pending":' in CALLBACK_SOURCE
    assert "order.status = \"paid\"" in CALLBACK_SOURCE
    assert "admin.subscription_expires_at = add_subscription_months(" in CALLBACK_SOURCE
    assert "await db.commit()" in CALLBACK_SOURCE
    assert CALLBACK_SOURCE.count('if order.status == "pending":') == 1


@pytest.mark.asyncio
async def test_real_result_endpoint_credits_once_and_does_not_extend_twice(monkeypatch):
    monkeypatch.setattr(settings, "uploads_dir", "/tmp/maxibot-test-uploads")
    from app import main
    monkeypatch.setattr(settings, "robokassa_test_mode", True)
    monkeypatch.setattr(settings, "robokassa_test_password2", "isolated-test-password")
    invoice_id = "54321"
    amount = "1900.00"
    from hashlib import md5

    signature = md5(f"{amount}:{invoice_id}:isolated-test-password".encode()).hexdigest()
    query = f"InvId={invoice_id}&OutSum={quote(amount)}&SignatureValue={signature}"
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/payments/robokassa/result",
        "query_string": query.encode(),
        "headers": [],
        "scheme": "https",
        "server": ("test", 443),
        "client": ("test", 1),
    })
    admin = SimpleNamespace(subscription_expires_at=None)
    order = SimpleNamespace(status="pending", invoice_id=int(invoice_id), admin_id="admin-1", months=1, paid_at=None)

    class FakeDb:
        commits = 0

        async def scalar(self, statement):
            return order

        async def get(self, model, object_id):
            return admin

        async def commit(self):
            self.commits += 1

    db = FakeDb()
    first = await main.robokassa_result(request, db)
    first_expiry = admin.subscription_expires_at
    second = await main.robokassa_result(request, db)
    assert first.body == f"OK{invoice_id}".encode()
    assert second.body == f"OK{invoice_id}".encode()
    assert order.status == "paid"
    assert first_expiry is not None
    assert admin.subscription_expires_at == first_expiry
    assert db.commits == 1
