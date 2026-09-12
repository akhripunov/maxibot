from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bot_ai_reuses_existing_analysis_pipeline_and_object_scope():
    source = (ROOT / "app/ads/service.py").read_text()
    assert "async def run_bot_check" in source
    assert "build_v2_analysis_dataset" in source
    assert 'object_type="BOT"' in source
    assert "promotion_object_id" in source


def test_bot_decision_engine_uses_bot_started_not_provider_conversions():
    source = (ROOT / "app/ads/directologist.py").read_text()
    assert 'BotConversion.internal_event == "bot_started"' in source
    assert '"source_of_truth": "TrafficMembership/max_subscriber or BotConversion/bot_started; Metrika is separate"' in source
    assert "BotTrafficVisit.bot_id" in source


def test_bot_ai_snapshot_and_memory_are_bounded_and_separate():
    models = (ROOT / "app/db/models.py").read_text()
    memory = (ROOT / "app/ads/memory.py").read_text()
    assert "promotion_object_type" in models
    assert "promotion_object_id" in models
    assert "if not proven_outcome" in memory


def test_system_bot_ai_promotion_uses_unified_primary_controls_and_keeps_refresh_route():
    menu = (ROOT / "app/bot/menu.py").read_text()
    runtime = (ROOT / "app/bot/runtime.py").read_text()
    assert 'ads_unified_buttons("ads_bot", "system", "promotion_maxibot", objective_id=profile.id, execution_mode=dashboard.get("execution_mode"), recommendation_id=' in menu
    for label in ("↻ Обновить сейчас", "📊 Кампании", "🎨 Креативы", "🔍 Почему", "🧠 Знания", "🕘 История"):
        assert label in menu
    assert '"✅ Я внедрил"' not in menu
    assert 'action.startswith("ads_bot_check:")' in runtime
    assert 'action.startswith(("ads_bot_campaigns:"' in runtime


def test_objective_screen_uses_client_copy_and_only_install_back_controls():
    menu = (ROOT / "app/bot/menu.py").read_text()
    assert '"🎯 Цель продвижения"' in menu
    assert '"📣 Канал: " + channel.title' in menu
    assert '"Цель помогает AI сравнивать фактическую стоимость"' in menu
    assert '"подписчика с вашим ориентиром и готовить более точные"' in menu
    assert '"рекомендации по продвижению."' in menu
    assert '("🎯 Установить цель", f"ads_objective_edit:{object_type}:{object_id}:all")' in menu
    assert '("⬅️ Назад", back)' in menu
    assert '"✏️ Изменить цель"' not in menu
    assert '"🚫 Отключить цель"' not in menu
    assert '"Результат: ' not in menu
    assert '"Цель применяется только к выбранным активным кампаниям."' not in menu


def test_bot_ai_migration_is_existing_table_only():
    migration = (ROOT / "alembic/versions/0078_ads_bot_ai_support_v1.py").read_text()
    assert "create_table" not in migration
    for table in ("ads_analysis_snapshots", "ads_ai_runs", "ads_memories", "ads_data_health"):
        assert f'"{table}"' in migration
