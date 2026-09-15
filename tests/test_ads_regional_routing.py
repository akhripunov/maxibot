import pytest

from app.ads.regional import AdsRegionalError, assert_execution_supported, assert_storage_supported, assert_remote_execution_configured, resolve_ads_account_route
from app.db.models import AdsAccount


def test_accounts_default_to_ru_and_route_is_provider_neutral():
    account = AdsAccount(id="a", admin_id="admin", provider="TELEGRAM_ADS")
    route = resolve_ads_account_route(account)
    assert (route.storage_region, route.execution_region) == ("RU", "RU")
    assert_execution_supported(account)


def test_nonlocal_execution_fails_closed_without_ru_fallback():
    account = AdsAccount(id="a", admin_id="admin", provider="TELEGRAM_ADS", storage_region="RU", execution_region="EU")
    with pytest.raises(AdsRegionalError, match="EXECUTION_REGION_UNAVAILABLE"):
        assert_execution_supported(account)


def test_eu_remote_requires_explicit_enabled_mtls_config():
    account = AdsAccount(id="a", admin_id="admin", provider="TELEGRAM_ADS", storage_region="RU", execution_region="EU")
    with pytest.raises(AdsRegionalError, match="REMOTE_EXECUTOR_NOT_READY"):
        assert_remote_execution_configured(account, enabled=False, endpoint=None, ca_file=None, client_cert_file=None, client_key_file=None)


def test_nonlocal_storage_fails_closed():
    account = AdsAccount(id="a", admin_id="admin", provider="YANDEX_DIRECT", storage_region="EU", execution_region="RU")
    with pytest.raises(AdsRegionalError, match="STORAGE_REGION_UNAVAILABLE"):
        assert_storage_supported(account)


@pytest.mark.parametrize("field", ["storage_region", "execution_region"])
def test_invalid_region_is_rejected(field):
    values = {"storage_region": "RU", "execution_region": "RU", field: "MARS"}
    with pytest.raises(AdsRegionalError, match=f"INVALID_{field.upper()}"):
        resolve_ads_account_route(AdsAccount(id="a", admin_id="admin", **values))
