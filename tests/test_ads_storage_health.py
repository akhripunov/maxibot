from app.ads.storage_health import human_size, storage_status, ui_time


def test_product_home_contract_has_no_storage_surface():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    home = source[source.index('if screen == "home":'):source.index('if screen == "ads_storage":')]
    assert "💾 ADS" not in home
    assert "свободно" not in home
    assert "Хранилище ADS" not in home


def test_storage_detail_has_explicit_owner_guard():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    detail = source[source.index('if screen == "ads_storage":'):source.index('if screen == "about":')]
    assert "not is_maxibot_owner(admin)" in detail
    assert '("⬅️ Назад", "owner_admin")' in detail


def test_owner_admin_is_the_only_storage_button_context():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    home = source[source.index('if screen == "home":'):source.index('if screen == "ads_storage":')]
    owner = source[source.index('if screen == "owner_admin":'):source.index('if screen == "support_prompt":')]
    assert "💾 Хранилище ADS" not in home
    assert "💾 Хранилище ADS" in owner
    assert "ads_storage" in owner


def test_platform_admin_command_is_separate_from_workspace_home():
    from pathlib import Path
    runtime = Path("app/bot/runtime.py").read_text()
    admin = runtime[runtime.index('if self._is_admin_command(normalized_text):'):runtime.index('admin_id = actor_id if await self.repo.is_admin(actor_id)')]
    assert "is_platform_admin" in admin
    assert '"owner_admin"' in admin


def test_storage_labels_are_russian_and_zero_states_are_explicit():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    detail = source[source.index('if screen == "ads_storage":'):source.index('if screen == "about":')]
    for label in ("Кампаний в сборе", "Повторных попыток в очереди", "Кампаний с устаревшими данными", "Защищённых строк", "Архивация данных"):
        assert label in detail
    assert "Ошибки: {periodic.get('failed_runs', 0)}" in detail


def test_storage_technical_details_are_available_behind_details_button():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    detail = source[source.index('if screen == "ads_storage":'):source.index('if screen == "about":')]
    main, subviews = detail.split('if screen.startswith("ads_storage_"):', 1)
    assert '("ℹ️ Подробнее", "ads_storage_details")' in main
    assert "Наблюдения:" not in main
    assert 'screen == "ads_storage_details"' in subviews
    assert "Наблюдения:" in subviews


def test_storage_screen_renders_all_canonical_tables_with_bounded_metadata():
    from pathlib import Path
    source = Path("app/bot/menu.py").read_text()
    detail = source[source.index('if screen == "ads_storage":'):source.index('if screen == "about":')]
    assert 'storage["table_details"]' in detail
    for label in ("строк", "latest", "retention", "status"):
        assert label in detail


def test_human_size_is_user_facing():
    assert human_size(824633720) == "786.4 МБ"
    assert human_size(23622320128) == "22.0 ГБ"


def test_storage_thresholds_are_centralized():
    assert storage_status(free_percent=50) == "NORMAL"
    assert storage_status(free_percent=10) == "WARNING"
    assert storage_status(free_percent=5) == "CRITICAL"
    assert storage_status(free_percent=50, stale=1) == "CRITICAL"


def test_storage_times_are_moscow_user_time():
    assert ui_time("2026-09-03T06:40:00+00:00") == "03.09 09:40"


def test_stale_health_is_scoped_to_recent_completed_campaign_runs():
    from pathlib import Path
    source = Path("app/ads/periodic.py").read_text()
    stale = source[source.index("recent_campaign ="):source.index("return {", source.index("recent_campaign ="))]
    assert 'collection_type == "CAMPAIGN"' in stale
    assert 'status == "COMPLETED"' in stale
    assert "completed_at >= now" in stale
    assert "~exists(recent_campaign)" in stale
    assert "outerjoin(AdsV2CollectionRun" not in stale


def test_storage_baseline_is_safe_and_tracks_only_diagnostic_metrics():
    from pathlib import Path
    source = Path("scripts/ads_storage_baseline.py").read_text()
    assert "ads_relation_bytes" in source
    assert "database_bytes" in source
    assert '"observations"' in source and '"report_runs"' in source
    assert '"collection_runs"' in source and '"entities"' in source
    assert "--output" in source
    assert "db.delete" not in source
    assert "VACUUM" not in source.upper()
