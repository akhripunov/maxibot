from types import SimpleNamespace

from app.web_admin_readonly import _successful_metrika


def test_uploaded_offline_conversion_is_not_reported_as_success():
    row = SimpleNamespace(
        sent_at=None,
        status="uploaded",
        metrica_upload_status="UPLOADED",
    )
    assert not _successful_metrika(row)


def test_only_processed_offline_conversion_is_reported_as_success():
    row = SimpleNamespace(
        sent_at="2026-09-09T18:00:00Z",
        status="sent",
        metrica_upload_status="PROCESSED",
    )
    assert _successful_metrika(row)


def test_linkage_failure_is_not_reported_as_success():
    row = SimpleNamespace(
        sent_at=None,
        status="linkage_failure",
        metrica_upload_status="LINKAGE_FAILURE",
    )
    assert not _successful_metrika(row)
