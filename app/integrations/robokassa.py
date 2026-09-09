"""Robokassa payment URL and callback signature helpers."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from urllib.parse import urlencode

from app.core.config import settings

PLANS: dict[int, Decimal] = {
    1: Decimal("1900.00"),
    3: Decimal("5130.00"),
    6: Decimal("7980.00"),
}


def configured() -> bool:
    password1 = settings.robokassa_test_password1 if settings.robokassa_test_mode else settings.robokassa_password1
    password2 = settings.robokassa_test_password2 if settings.robokassa_test_mode else settings.robokassa_password2
    return bool(settings.robokassa_merchant_login and password1 and password2)


def amount_for_months(months: int) -> Decimal:
    try:
        return PLANS[months]
    except KeyError as exc:
        raise ValueError("unsupported subscription period") from exc


def _hash(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def payment_url(*, invoice_id: int, amount: Decimal, description: str) -> str:
    if not configured():
        raise RuntimeError("Robokassa is not configured")
    amount_text = f"{amount:.2f}"
    password1 = settings.robokassa_test_password1 if settings.robokassa_test_mode else settings.robokassa_password1
    signature = _hash(f"{settings.robokassa_merchant_login}:{amount_text}:{invoice_id}:{password1}")
    params = {
        "MerchantLogin": settings.robokassa_merchant_login,
        "OutSum": amount_text,
        "InvId": str(invoice_id),
        "Description": description,
        "SignatureValue": signature,
        "IsTest": "1" if settings.robokassa_test_mode else "0",
        "Encoding": "utf-8",
    }
    return f"{settings.robokassa_payment_url}?{urlencode(params)}"


def valid_result_signature(*, amount: str, invoice_id: str, signature: str) -> bool:
    password2 = settings.robokassa_test_password2 if settings.robokassa_test_mode else settings.robokassa_password2
    if not password2:
        return False
    expected = _hash(f"{amount}:{invoice_id}:{password2}")
    return expected.lower() == (signature or "").strip().lower()
