from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_SOURCE = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
MENU_SOURCE = (ROOT / "app" / "bot" / "menu.py").read_text(encoding="utf-8")
ATTRIBUTION_SOURCE = (ROOT / "app" / "traffic" / "attribution.py").read_text(encoding="utf-8")


def test_owner_funnel_report_exposes_counts_not_click_identifiers():
    report = MAIN_SOURCE[
        MAIN_SOURCE.index('@app.get("/admin/traffic/funnel-report")'):
        MAIN_SOURCE.index('@app.get("/api/channels")')
    ]
    assert "direct_attributed_unique_yclid" in report
    assert "duplicate_or_revisit_requests" in report
    assert '"yclid": visit.yclid' not in report
    assert '"startapp_token"' not in report
    assert '"max_user_id"' not in report


def test_only_exact_verified_startapp_identity_can_queue_yandex():
    assert "return status == EXACT" in ATTRIBUTION_SOURCE
    membership_handler = MENU_SOURCE[
        MENU_SOURCE.index("async def record_traffic_membership"):
        MENU_SOURCE.index("async def is_admin")
    ]
    assert "EXACT_VERIFIED_STARTAPP_IDENTITY" in membership_handler
    assert "NO_VERIFIED_STARTAPP_IDENTITY" in membership_handler
    assert "MULTIPLE_VERIFIED_IDENTITY_VISITS" in membership_handler
    assert "if decision.status == EXACT and decision.visit_id:" in membership_handler
    assert "if decision.status in {EXACT, HIGH}" not in membership_handler
    attributions = MAIN_SOURCE[
        MAIN_SOURCE.index('@app.get("/admin/traffic/attributions")'):
        MAIN_SOURCE.index('@app.get("/admin/traffic/funnel-report")')
    ]
    assert '"conversion_eligible": membership.attribution_status == "EXACT"' in attributions
