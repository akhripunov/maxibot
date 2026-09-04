import pytest

from app.ads.periodic import _entity_config_rows
from app.ads.providers.base import ProviderReport


def test_entity_config_reads_provider_report_rows_without_iterating_report():
    report = ProviderReport(provider="YANDEX_DIRECT", report_type="ADS", rows=[{"Id": 123, "Status": "ACCEPTED"}])
    assert _entity_config_rows(report) == [{"Id": 123, "Status": "ACCEPTED"}]


def test_entity_config_rejects_malformed_report_shape_precisely():
    with pytest.raises(TypeError, match="ENTITY_CONFIG_ROWS_NOT_LIST"):
        _entity_config_rows(object())


def test_entity_config_rejects_malformed_row_shape_precisely():
    report = ProviderReport(provider="YANDEX_DIRECT", report_type="ADS", rows=["not-an-object"])
    with pytest.raises(TypeError, match="ENTITY_CONFIG_ROW_NOT_OBJECT"):
        _entity_config_rows(report)
