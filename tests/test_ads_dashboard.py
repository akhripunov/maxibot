from types import SimpleNamespace

from datetime import date, datetime, timezone
from math import isclose
import pytest

from app.ads.dashboard import (
    _campaign_to_date_spend,
    _count_campaign_exact_memberships,
    _experiment_status_label,
    _is_user_history_business_experiment,
    _sum_active_campaign_spend,
    business_cpa,
    stage_for_state,
)
from app.bot.menu import (
    format_ads_campaigns_screen,
    format_ads_history_screen,
    format_ads_learning_screen,
    format_ads_unified_dashboard,
    ads_campaign_selection_buttons,
    format_ads_why_screen,
)


def test_dashboard_query_imports_sqlalchemy_or_for_object_scoped_recommendations():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "ads" / "dashboard.py").read_text()
    assert "from sqlalchemy import or_, select" in source


def test_exact_campaign_memberships_do_not_require_a_snapshot_or_rolling_window():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "ads" / "dashboard.py").read_text()
    exact_metrics = source.split("exact_campaign_ids =", 1)[1].split("campaign_mapping =", 1)[0]
    assert "len(exact_campaign_ids) == 1" in exact_metrics
    assert "and metric_snapshots" not in exact_metrics
    assert "AdsAnalysisSnapshot" not in exact_metrics
    assert 'TrafficMembership.attribution_status == "EXACT"' in exact_metrics
    assert "campaign_matches" not in exact_metrics  # delegated to tested campaign-scoped helper
    assert "now()-7d" in exact_metrics


def test_business_spend_uses_campaign_lifetime_daily_aggregates():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "ads" / "dashboard.py").read_text()
    spend_query = source.split("canonical_spend:", 1)[1].split("latest_payload =", 1)[0]
    assert "AdsDailyAggregate.aggregate_date" in spend_query
    assert "AdsDailyAggregate.dimension_type == \"CAMPAIGN\"" in spend_query
    assert "AdsDailyAggregate.dimension_value == campaign.external_id" in spend_query
    assert "AdsObservation.window == \"7d\"" not in spend_query
    assert "snapshot_spend" not in spend_query


def test_campaign_to_date_totals_survive_empty_recent_window_after_pause():
    # These canonical daily rows are older than seven days; no recent rows are
    # needed for the accumulated UI summary to retain its history.
    rows = [
        SimpleNamespace(cost=40.0, aggregate_date=date(2026, 9, 1)),
        SimpleNamespace(cost=45.71, aggregate_date=date(2026, 9, 10)),
    ]
    spend, period_from, period_to = _campaign_to_date_spend(rows)
    memberships = [
        (SimpleNamespace(attribution_status="EXACT", joined_at=datetime(2026, 9, 1, tzinfo=timezone.utc)), "714373352"),
        (SimpleNamespace(attribution_status="EXACT", joined_at=datetime(2026, 9, 10, tzinfo=timezone.utc)), "x.714373352.variant"),
        (SimpleNamespace(attribution_status="EXACT", joined_at=datetime(2026, 9, 11, tzinfo=timezone.utc)), "714373352"),
        (SimpleNamespace(attribution_status="EXACT"), "another-campaign"),
        (SimpleNamespace(attribution_status="MODELED"), "714373352"),
    ]
    outcomes = _count_campaign_exact_memberships(memberships, "714373352")
    assert isclose(spend, 85.71)
    assert (period_from, period_to) == (date(2026, 9, 1), date(2026, 9, 10))
    assert outcomes == 3
    assert isclose(business_cpa(spend=spend, confirmed_outcomes=outcomes), 28.57)


def test_lifetime_spend_does_not_hardcode_campaign_values_or_sum_unrelated_rows():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "ads" / "dashboard.py").read_text()
    assert "714373352" not in source
    records = {
        "linked": (12.5, date(2026, 1, 1), date(2026, 1, 2)),
        "unrelated": (999.0, date(2026, 1, 1), date(2026, 1, 2)),
    }
    assert _sum_active_campaign_spend(records, {"linked"}) == 12.5


def test_business_cpa_fails_closed_for_unknown_or_zero_outcomes():
    assert business_cpa(spend=148, confirmed_outcomes=43) == 148 / 43
    assert business_cpa(spend=148, confirmed_outcomes=0) is None
    assert business_cpa(spend=None, confirmed_outcomes=43) is None


@pytest.mark.asyncio
async def test_dashboard_render_handles_paused_campaign_lifetime_metrics(monkeypatch):
    """Exercise the same full dashboard projection used by the AI Promotion button."""
    from datetime import date, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.ads.dashboard import build_ads_dashboard
    from app.db.models import (
        Admin, AdsAccount, AdsAiOutcome, AdsAiRun, AdsCampaign, AdsDailyAggregate, AdsEntity,
        AdsRecommendation, AdsPromotionCampaignLink, Channel, LandingPage,
        TrafficMembership, TrafficVisit, Base,
    )

    async def empty_metrics(*args, **kwargs):
        return {"7d": {"qualified_memberships": 0}, "campaign_specific": None}

    monkeypatch.setattr("app.ads.attribution.build_maxibot_metrics", empty_metrics)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            admin = Admin(external_id="admin-dashboard-regression")
            db.add(admin)
            await db.flush()
            channel = Channel(admin_id=admin.id, external_id="channel-dashboard-regression",
                              title="Regression", public_slug="dashboard-regression")
            db.add(channel)
            account = AdsAccount(admin_id=admin.id, external_account_id="account-regression")
            db.add(account)
            await db.flush()
            campaign = AdsCampaign(account_id=account.id, external_id="campaign-regression",
                                   name="Paused campaign", state="SUSPENDED", status="SUSPENDED")
            db.add(campaign)
            await db.flush()
            db.add(AdsPromotionCampaignLink(object_type="CHANNEL", channel_id=channel.id,
                                            campaign_id=campaign.id, status="ACTIVE"))
            entity = AdsEntity(provider="YANDEX_DIRECT", entity_type="CAMPAIGN",
                               provider_entity_id=campaign.external_id, campaign_id=campaign.id)
            db.add(entity)
            landing = LandingPage(channel_id=channel.id, public_id="dashboard-regression",
                                  title="Regression", description="Regression")
            db.add(landing)
            await db.flush()
            canary_run = AdsAiRun(channel_id=channel.id, promotion_object_type="CHANNEL",
                                  promotion_object_id=channel.id, campaign_id=campaign.id,
                                  status="COMPLETED")
            db.add(canary_run)
            await db.flush()
            canary = AdsRecommendation(
                ai_run_id=canary_run.id, channel_id=channel.id,
                promotion_object_type="CHANNEL", promotion_object_id=channel.id,
                experiment_id="da163e5d-7b97-4fb6-84f6-42901b288236", experiment_seq=1,
                experiment_kind="TECHNICAL_CANARY", experiment_status="completed",
                status="OPEN", title="Technical canary: weekly budget apply and rollback",
                hypothesis="Provider accepts a bounded weekly budget update and exact rollback without strategy drift.",
                action_code="BOUNDED_BUDGET_REALLOCATION", data_sufficiency="SUFFICIENT",
                provider_lifecycle="ROLLED_BACK",
            )
            db.add(canary)
            await db.flush()
            db.add(AdsAiOutcome(
                recommendation_id=canary.id, experiment_id=canary.experiment_id,
                horizon="7d", due_at=datetime.now(timezone.utc), status="COMPLETED",
                execution_status="ROLLED_BACK", implementation_confirmed="YES",
                evaluation_status="TECHNICAL_PASS",
            ))
            rec_only = AdsRecommendation(
                ai_run_id=canary_run.id, channel_id=channel.id,
                promotion_object_type="CHANNEL", promotion_object_id=channel.id,
                experiment_id="legacy-recommendation", experiment_seq=2,
                experiment_status="completed", status="OPEN", title="Old HOLD recommendation",
                action_code="HOLD", data_sufficiency="INSUFFICIENT",
            )
            actual = AdsRecommendation(
                ai_run_id=canary_run.id, channel_id=channel.id,
                promotion_object_type="CHANNEL", promotion_object_id=channel.id,
                experiment_id="actual-business-experiment", experiment_seq=1,
                experiment_status="completed", status="OPEN", title="Implemented test",
                action_code="EXCLUDE_PLACEMENT", data_sufficiency="SUFFICIENT",
            )
            db.add_all([rec_only, actual])
            await db.flush()
            db.add(AdsAiOutcome(
                recommendation_id=rec_only.id, experiment_id=rec_only.experiment_id,
                horizon="7d", due_at=datetime.now(timezone.utc), status="COMPLETED",
                execution_status="RECOMMENDATION_ONLY", implementation_confirmed="UNKNOWN",
                evaluation_status="INCONCLUSIVE",
            ))
            db.add(AdsAiOutcome(
                recommendation_id=actual.id, experiment_id=actual.experiment_id,
                horizon="7d", due_at=datetime.now(timezone.utc), status="COMPLETED",
                execution_status="IMPLEMENTED", implementation_confirmed="YES",
                evaluation_status="INCONCLUSIVE", completed_at=datetime.now(timezone.utc),
                implementation_evidence_json='{"executed_at":"2026-01-02T03:04:05+00:00"}',
            ))
            db.add_all([
                AdsDailyAggregate(provider="YANDEX_DIRECT", campaign_id=campaign.id,
                                  entity_id=entity.id, dimension_type="CAMPAIGN",
                                  dimension_value=campaign.external_id,
                                  aggregate_date=date.today() - timedelta(days=20), cost=60),
                AdsDailyAggregate(provider="YANDEX_DIRECT", campaign_id=campaign.id,
                                  entity_id=entity.id, dimension_type="CAMPAIGN",
                                  dimension_value=campaign.external_id,
                                  aggregate_date=date.today() - timedelta(days=10), cost=30),
            ])
            for idx in range(2):
                visit = TrafficVisit(visit_token=f"dashboard-regression-{idx}",
                                     landing_page_id=landing.id, channel_id=channel.id,
                                     utm_campaign="campaign-regression",
                                     landing_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                db.add(visit)
                await db.flush()
                db.add(TrafficMembership(channel_id=channel.id, max_user_id=f"user-{idx}",
                                         attributed_visit_id=visit.id,
                                         attribution_status="EXACT",
                                         joined_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
            await db.commit()

            dashboard = await build_ads_dashboard(
                db, object_type="CHANNEL", object_id=channel.id,
            )
            assert dashboard["confirmed_business_outcomes"] == 2
            assert dashboard["actual_business_cpa"] == 45
            assert dashboard["current_experiment"] is None
            assert dashboard["last_completed_experiment"]["experiment_id"] == "actual-business-experiment"
            assert dashboard["learning_summary"]["total_experiments"] == 1
            assert [item["experiment_id"] for item in dashboard["history"]] == ["actual-business-experiment"]
            actual_history = dashboard["history"][0]
            assert actual_history["status_label"] == "⚪ Завершён"
            assert actual_history["evaluation_status"] == "INCONCLUSIVE"
            assert actual_history["started_at"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            assert await db.get(AdsRecommendation, canary.id) is not None
    finally:
        await engine.dispose()


def test_current_experiment_requires_live_business_lifecycle_and_nonterminal_execution():
    from app.ads.dashboard import _is_current_business_experiment

    business = SimpleNamespace(experiment_kind="STANDARD", experiment_status="running")
    implemented = SimpleNamespace(
        implementation_confirmed="YES", execution_status="IMPLEMENTED", evaluation_status="PENDING",
    )
    assert _is_current_business_experiment(business, implemented)

    # Terminal lifecycle states, outcomes, and technical experiment types are
    # historical regardless of a still-open recommendation row.
    terminal_cases = [
        ("TECHNICAL_CANARY", "completed", "ROLLED_BACK", "TECHNICAL_PASS"),
        ("STANDARD", "completed", "IMPLEMENTED", "PENDING"),
        ("STANDARD", "rolled_back", "ROLLED_BACK", "PENDING"),
        ("STANDARD", "running", "ROLLED_BACK", "PENDING"),
        ("STANDARD", "evaluating", "IMPLEMENTED", "INCONCLUSIVE"),
        ("STANDARD", "evaluating", "IMPLEMENTED", "TECHNICAL_PASS"),
        ("STANDARD", "evaluating", "IMPLEMENTED", "EARLY_STOP"),
        ("STANDARD", "evaluating", "PROVIDER_REJECTED", "PROVIDER_REJECTED"),
        ("STANDARD", "planned", "IMPLEMENTED", "PENDING"),
    ]
    for kind, lifecycle, execution, evaluation in terminal_cases:
        recommendation = SimpleNamespace(experiment_kind=kind, experiment_status=lifecycle)
        outcome = SimpleNamespace(implementation_confirmed="YES", execution_status=execution,
                                  evaluation_status=evaluation)
        assert not _is_current_business_experiment(recommendation, outcome), (kind, lifecycle, execution, evaluation)


def test_user_history_requires_business_type_and_confirmed_execution():
    outcome = SimpleNamespace(implementation_confirmed="YES", execution_status="IMPLEMENTED")
    actual = SimpleNamespace(experiment_kind=None, experiment_id="e1", experiment_seq=1)
    technical = SimpleNamespace(experiment_kind="TECHNICAL_CANARY", experiment_id="e2", experiment_seq=2)
    recommendation = SimpleNamespace(experiment_kind=None, experiment_id="e3", experiment_seq=3)
    assert _is_user_history_business_experiment(actual, [outcome])
    assert not _is_user_history_business_experiment(technical, [outcome])
    assert not _is_user_history_business_experiment(recommendation, [
        SimpleNamespace(implementation_confirmed="UNKNOWN", execution_status="RECOMMENDATION_ONLY")
    ])


def test_experiment_history_lifecycle_is_separate_from_inconclusive_evaluation():
    completed = {"experiment_status": "completed", "execution_status": "IMPLEMENTED",
                 "evaluation_status": "INCONCLUSIVE", "implementation_confirmed": "YES"}
    active = {"experiment_status": "running", "execution_status": "IMPLEMENTED",
              "evaluation_status": "PENDING", "implementation_confirmed": "YES"}
    rolled_back = {"experiment_status": "rolled_back", "execution_status": "ROLLED_BACK",
                   "evaluation_status": "INCONCLUSIVE", "implementation_confirmed": "YES"}
    assert _experiment_status_label(completed) == "⚪ Завершён"
    assert _experiment_status_label(active) == "🟡 Идёт"
    assert _experiment_status_label(rolled_back) == "↩️ Откат выполнен"


def test_dashboard_spend_ignores_active_campaigns_without_metric_rows():
    assert _sum_active_campaign_spend({"campaign-1": None}, {"campaign-1"}) is None
    assert _sum_active_campaign_spend({
        "campaign-1": None,
        "campaign-2": (85.71, None),
        "campaign-3": (None, None),
        "campaign-4": (500, None),
    }, {"campaign-1", "campaign-2", "campaign-3"}) == 85.71


def test_stage_mapping_uses_closed_loop_state():
    rec = SimpleNamespace(status="OPEN", data_sufficiency="SUFFICIENT")
    outcome = SimpleNamespace(implementation_confirmed="UNKNOWN", evaluation_status=None)
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "RECOMMENDATION"
    outcome.implementation_confirmed = "YES"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "CHECK"
    outcome.evaluation_status = "SUCCESS"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "RESULT"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=True) == "SCALE"


def test_unified_screen_has_single_action_and_read_only_navigation():
    from app.bot.menu import ads_unified_buttons, format_ads_unified_dashboard

    state = {
        "object": {"type": "BOT", "id": "bot-1"},
        "objective": {"type": "bot_started"}, "target_cpa": None,
        "actual_business_cpa": None, "confirmed_business_outcomes": 0,
        "creative_summary": {"active": 0, "attention": 0},
        "current_stage": "ANALYSIS", "primary_root_cause": None,
        "why_summary": None, "next_best_action": "Продолжать сбор данных.",
        "data_trust": "UNKNOWN", "last_updated_at": None,
    }
    text = format_ads_unified_dashboard(state, title="Test bot")
    assert "Фактический CPA: пока не рассчитан" in text
    assert "🚀 Автопилот выключен" in text
    assert "💡 Статус: Рекомендации" in text
    assert "Пока ничего менять не нужно." not in text
    assert "🧠 Знания: пока нет" in text
    assert "Data Trust" not in text
    labels = [button["text"] for row in ads_unified_buttons("ads_bot", "system", "back") for button in row]
    assert "↻ Обновить сейчас" in labels and "🎨 Креативы" in labels
    assert "Проверить рекламу" not in labels and "Рекомендации" not in labels


def test_unified_screen_renders_canonical_exact_subscribers_and_business_cpa():
    state = {
        "object": {"type": "CHANNEL", "id": "channel-1"},
        "objective": {"type": "max_subscriber"}, "target_cpa": 20,
        "currency": "RUB", "actual_business_cpa": 85.71 / 3,
        "confirmed_business_outcomes": 3, "creative_summary": {"active": 0, "attention": 0},
        "current_stage": "ANALYSIS", "analysis_running": False,
        "data_trust": "HEALTHY", "active_campaigns": [],
        "learning_state": {"status": "EXPLORE", "verified_count": 0},
        "memories": [], "memory_evidence_counts": {}, "history": [], "creatives": [],
    }
    text = format_ads_unified_dashboard(state, title="Москва 365")
    assert "Фактический CPA: 28,57 ₽" in text
    assert "Подтверждённых подписчиков: 3" in text


def _state(stage="ANALYSIS", *, root=None, outcome=None, proven=False):
    return {
        "object": {"type": "CHANNEL", "id": "channel-1"},
        "objective": {"type": "max_subscriber"}, "target_cpa": 100,
        "currency": "RUB", "actual_business_cpa": None,
        "confirmed_business_outcomes": 0, "creative_summary": {"active": 4, "attention": 0},
        "current_stage": stage, "analysis_running": False, "primary_root_cause": root,
        "root_cause_status": "PROVEN" if root else "NOT_PROVEN", "why_summary": "Данные подтверждают сигнал.",
        "next_best_action": "Продолжать сбор данных.", "data_trust": "HEALTHY",
        "last_updated_at": None, "current_recommendation": None, "current_outcome": outcome,
        "active_campaigns": [], "learning_state": {"status": "PROVEN" if proven else "EXPLORE", "verified_count": 0},
        "memories": [], "memory_evidence_counts": {}, "history": [], "creatives": [],
    }


def test_unified_screen_renders_recommendations_and_autopilot_states():
    insufficient = format_ads_unified_dashboard(_state(), title="Москва 365")
    assert "Фактический CPA: пока не рассчитан" in insufficient
    assert "💡 Статус: Рекомендации" in insufficient
    assert "Пока ничего менять не нужно." not in insufficient

    recommendation_state = _state("RECOMMENDATION", root="📍 Площадка site.ru")
    recommendation_state["current_recommendation"] = SimpleNamespace(title="Отключить площадку site.ru")
    recommendation = format_ads_unified_dashboard(recommendation_state, title="Москва 365")
    assert "💡 Рекомендация" in recommendation
    assert "Отключить площадку site.ru" in recommendation

    autopilot = _state()
    autopilot["execution_mode"] = "AUTONOMOUS"
    autopilot["current_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": None, "before": 148, "after": 121}
    autopilot["learning_summary"] = {"total_experiments": 12, "successful_experiments": 4, "proven_memories": 3}
    running = format_ads_unified_dashboard(autopilot, title="Москва 365")
    assert "🚀 Автопилот включён" in running
    assert "Эксперимент #18" in running and "Новый креатив №4" in running
    assert "🧠 Знания: 3 подтверждено · 0 проверяется · 0 отклонено" in running

    success_state = _state()
    success_state["execution_mode"] = "AUTONOMOUS"
    success_state["last_completed_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": "SUCCESS", "before": 148, "after": 118}
    success = format_ads_unified_dashboard(success_state, title="Москва 365")
    assert "✅ Эксперимент #18 успешен" in success

    failed_state = _state()
    failed_state["execution_mode"] = "AUTONOMOUS"
    failed_state["last_completed_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": "FAIL", "before": 148, "after": 176, "rollback": True}
    failed = format_ads_unified_dashboard(failed_state, title="Москва 365")
    assert "↩️ MaxiBot откатил изменение." in failed


def test_autonomous_suspended_campaign_shows_paused_execution_and_master_flags():
    state = _state()
    state["execution_mode"] = "AUTONOMOUS"
    state["autopilot"] = {
        "execution_capability": "DISABLED", "campaign_state": "SUSPENDED",
        "execution_readiness": "BLOCKED_BY_CAMPAIGN_STATE",
        "ads_autopilot_enabled": False, "ads_write_actions_enabled": False,
    }
    text = format_ads_unified_dashboard(state, title="Москва 365")
    assert "🚀 Автопилот включён" in text
    assert "⏸ Выполнение приостановлено: кампания остановлена." in text
    assert "Системный автопилот: выключен" in text
    assert "Запись в Директ: выключена" in text
    assert "Автопилот работает" not in text


def test_detail_renderers_preserve_isolation_and_verified_learning_only():
    state = _state("RECOMMENDATION", root="🎨 Креатив A")
    state["active_campaigns"] = [{
        "external_id": "713798021", "active": True, "provider": "YANDEX_DIRECT",
        "id": "campaign-1", "name": "Основная", "linked": True,
        "spend": 148, "clicks": 43, "state": "ON", "recommendation": None,
    }]
    assert "713798021 — 🟢 Активна" in format_ads_campaigns_screen(state, title="Москва 365")
    assert ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["text"].startswith("✅")
    assert "ads_campaign_select:channel-1:campaign-1" == ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["callback_data"]
    state["active_campaigns"][0]["linked"] = False
    assert ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["text"].startswith("◻️")
    assert "🎨 Креатив A" in format_ads_why_screen(state, title="Москва 365")
    assert "Пока знаний нет." in format_ads_learning_screen(state, title="Москва 365")
    empty = format_ads_history_screen(state, title="Москва 365")
    assert "Реальных бизнес-экспериментов пока не было." in empty
    assert "Рекомендации и технические проверки здесь не отображаются." in empty


def test_history_projection_is_bounded_and_exposes_safe_detail_chain():
    state = _state()
    state["history"] = [{
        "experiment_id": "internal-id-must-not-render", "experiment_seq": 2,
        "status_label": "✅ Победа", "campaign_name": "Основная",
        "hypothesis": "Площадка улучшит CPA", "started_at": None,
        "completed_at": None, "delta": {"actual_cpa_7d": {"before": 20, "after": 15}},
        "evaluation_status": "SUCCESS", "objective": "userDidSubscribe",
        "action": "EXCLUDE_PLACEMENT", "rationale": "Данные по площадке",
        "memories": ["Площадка дала лучший CPA"],
    }]
    text = format_ads_history_screen(state, title="Москва 365")
    assert "🧪 Эксперимент #2" in text
    assert "✅ Победа" in text and "До: 20,00 ₽ · После: 15,00 ₽" in text
    assert "CPA: 20,00 ₽ → 15,00 ₽" not in text
    assert "internal-id-must-not-render" not in text
    detail = __import__("app.bot.menu", fromlist=["format_ads_experiment_detail"]).format_ads_experiment_detail(state["history"][0], title="Москва 365")
    assert "Что изменил автопилот" in detail and "Почему" in detail and "Что узнал автопилот" in detail


def test_history_excludes_legacy_rows_and_never_renders_none():
    state = _state()
    state["history"] = [
        {"experiment_id": "legacy-id", "experiment_seq": None, "title": "Старая проверка"},
        {"experiment_id": None, "experiment_seq": None, "title": "Рекомендация"},
    ]
    text = format_ads_history_screen(state, title="Москва 365")
    assert "Реальных бизнес-экспериментов пока не было." in text
    assert "Эксперимент #None" not in text
    assert "Старая проверка" not in text


def test_history_mixed_rows_renders_only_canonical_experiment():
    state = _state()
    state["history"] = [
        {"experiment_id": "legacy-id", "experiment_seq": None, "title": "Старая проверка"},
        {"experiment_id": "canonical-id", "experiment_seq": 1, "title": "Новый тест",
         "status_label": "✅ Успешно", "evaluation_status": "SUCCESS"},
    ]
    text = format_ads_history_screen(state, title="Москва 365")
    assert "🧪 Эксперимент #1" in text
    assert "Старая проверка" not in text
    assert "Эксперимент #None" not in text
    assert "SUCCESS" not in text


def test_knowledge_screen_separates_memory_status_and_maturity():
    state = _state()
    state["knowledge_summary"] = {"validated": 1, "supported": 1, "rejected": 1}
    state["knowledge"] = [
        {"status": "VALIDATED", "claim": "Рабочая гипотеза", "confidence": "HIGH", "source_experiments": [12], "maturity": "SCALE"},
        {"status": "SUPPORTED", "claim": "Предварительная гипотеза", "confidence": "MEDIUM", "source_experiments": [14], "maturity": "VALIDATE"},
        {"status": "REJECTED", "claim": "Отклонённая гипотеза", "confidence": "LOW", "source_experiments": [7, 9], "maturity": "EXPLORE"},
    ]
    text = format_ads_learning_screen(state, title="Москва 365")
    assert "🧠 Знания MaxiBot" in text
    assert "✅ Подтверждено" in text and "🟡 Проверяется" in text and "❌ Отклонено" in text
    assert "📈 Зрелость: 📈 Масштабируем" in text
    assert "Основано на: Эксперименты #14" in text
    assert "VALIDATED" not in text and "SUPPORTED" not in text and "REJECTED" not in text
def test_history_learning_chain_renders_persisted_causality_without_raw_memory_ids():
    state = _state()
    state["history"] = [{
        "experiment_id": "exp-13-internal", "experiment_seq": 13,
        "status_label": "🟡 Запланирован", "campaign_name": "Основная",
        "hypothesis": "Проверить новую площадку", "started_at": None,
        "completed_at": None, "delta": {}, "evaluation_status": "PENDING",
        "objective": "userDidSubscribe", "action": "EXCLUDE_PLACEMENT",
        "rationale": "Сравнить с подтверждённым сигналом",
        "why_selected": "На выбор повлияли сохранённые знания: эксперимент #12: Площадка снижает CPA",
        "influenced_by": [{"claim": "Площадка снижает CPA", "status": "SUPPORTED", "source_experiments": [12]}],
        "avoided_due_to_memory": [{"claim": "Старая гипотеза не работает", "status": "REJECTED", "source_experiments": [7, 9]}],
        "learned_memories": [], "next_experiment_seqs": [],
        "learning_chain": {"result": "PENDING", "learned": [], "used_in_next": []},
        "maturity_before": "EXPLORE", "maturity_after": "VALIDATE",
        "memories": [],
    }]
    detail = __import__("app.bot.menu", fromlist=["format_ads_experiment_detail"]).format_ads_experiment_detail(
        state["history"][0], title="Москва 365"
    )
    state["history"].append({
        "experiment_id": "exp-12-internal", "experiment_seq": 12,
        "status_label": "✅ Победа", "campaign_name": "Основная", "hypothesis": "Площадка снижает CPA",
        "started_at": None, "completed_at": None, "delta": {}, "evaluation_status": "SUCCESS",
        "learned_memories": [{"claim": "Площадка снижает CPA", "status": "SUPPORTED"}],
        "next_experiment_seqs": [13], "memories": [],
    })
    listing = format_ads_history_screen(state, title="Москва 365")
    assert "🧠 MaxiBot выучил:" in listing and "↓ Повлияло на следующий тест" in listing
    assert "Почему MaxiBot выбрал этот эксперимент" in detail
    assert "🧠 Использованные знания:" in detail
    assert "Эксперимент #12" in detail and "Площадка снижает CPA" in detail
    assert "🚫 Не стали повторять:" in detail and "эксперименты #7, #9" in detail.lower()
    # The display must expose only human experiment numbers, never technical IDs.
    assert "exp-13-internal" not in detail


def test_campaign_screen_toggle_only_changes_analysis_state():
    from app.bot.menu import format_ads_campaigns_screen

    base = {
        "id": "campaign-1", "external_id": "713798021", "name": "Основная",
        "active": True, "provider": "YANDEX_DIRECT", "state": "ON",
        "spend": 920, "clicks": 292, "analysis_enabled": True,
        "execution_mode": "AUTONOMOUS", "recommendation": SimpleNamespace(title="не показывать"),
    }
    before = format_ads_campaigns_screen({"active_campaigns": [base]}, title="Москва 365")
    after_row = {**base, "analysis_enabled": False}
    after = format_ads_campaigns_screen({"active_campaigns": [after_row]}, title="Москва 365")
    assert "✅ В анализе" in before and "⬜ Не в анализе" in after
    assert "Расход: 920,00 ₽" in before and "Расход: 920,00 ₽" in after
    assert "Клики: 292" in before and "Клики: 292" in after
    assert "Состояние: ON" in before and "Состояние: ON" in after
    assert "Рекомендация:" not in before and "Рекомендация:" not in after
    assert before.replace("✅ В анализе", "ANALYSIS").replace("⬜ Не в анализе", "ANALYSIS") == after.replace("✅ В анализе", "ANALYSIS").replace("⬜ Не в анализе", "ANALYSIS")


def test_unified_screen_shows_selected_campaign_numbers_when_autopilot_off():
    state = _state()
    state["active_campaigns"] = [
        {"id": "internal-1", "external_id": "713798021", "linked": True},
        {"id": "internal-2", "external_id": "713735649", "linked": False},
    ]
    text = format_ads_unified_dashboard(state, title="Москва 365")
    assert "🚀 Автопилот выключен" in text
    assert "Выбранные кампании: 713798021" in text
    assert "713735649" not in text

    state["active_campaigns"] = [{"id": "internal-2", "external_id": "713735649", "linked": False}]
    text = format_ads_unified_dashboard(state, title="Москва 365")
    assert "Выбранные кампании: не выбраны" in text


def test_main_screen_supports_human_running_and_inconclusive_experiments():
    human = _state()
    human["current_experiment"] = {
        "experiment_seq": 3, "action": "Отключить площадку site.ru", "executed_by": "HUMAN",
        "evaluation_status": "READY", "before": 148, "after": 121,
    }
    text = format_ads_unified_dashboard(human, title="Москва 365")
    assert "Сейчас проверяем:" in text and "Эксперимент #3" in text
    assert "До: 148 ₽" in text and "Сейчас: 121 ₽" in text

    inconclusive = _state()
    inconclusive["execution_mode"] = "AUTONOMOUS"
    inconclusive["current_experiment"] = {
        "experiment_seq": 4, "action": "Новый креатив", "evaluation_status": "INCONCLUSIVE",
        "before": 148, "after": 139,
    }
    text = format_ads_unified_dashboard(inconclusive, title="Москва 365")
    assert "⏳ Недостаточно данных." in text
    assert "❌ Эксперимент" not in text


def test_why_screen_explains_degraded_data_without_internal_enum():
    state = _state("RECOMMENDATION", root="Рекламная причина пока не доказана.")
    state["data_trust"] = "DEGRADED"
    state["data_trust_reasons"] = {"checks": {"ATTRIBUTION": "DEGRADED"}}
    text = format_ads_why_screen(state, title="Москва 365")
    assert "О качестве данных:" in text
    assert "Часть результатов пока нельзя надёжно связать" in text
    assert "Поэтому рекомендация предварительная." in text
    assert "Data Trust" not in text


def test_why_screen_uses_safe_generic_for_unknown_data():
    state = _state("ANALYSIS")
    state["data_trust"] = "UNKNOWN"
    state["data_trust_reasons"] = {}
    text = format_ads_why_screen(state, title="Москва 365")
    assert "Часть данных пока нельзя надёжно сопоставить" in text
    assert "UNKNOWN" not in text


def test_why_screen_suppresses_stale_mapping_warning_when_live_mapping_supported():
    from app.bot.menu import _data_trust_explanation

    state = {
        "data_trust": "DEGRADED",
        "data_trust_reasons": {"checks": {"ATTRIBUTION": "DEGRADED", "CAMPAIGN_MAPPING": "DEGRADED"}},
        "campaign_specific": {"supported": True, "coverage": 56 / 58},
    }
    assert _data_trust_explanation(state) is None


def test_dashboard_replaces_stale_recommendation_rationale_after_mapping_fix():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/dashboard.py").read_text()
    assert "stale_markers" in source
    assert "Подтверждённые подписчики сопоставлены с кампанией по UTM" in source


def test_dashboard_recomputes_mapping_from_one_active_link_even_if_provider_state_is_filtered():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/dashboard.py").read_text()
    assert "active_link_campaign_ids" in source
    assert "len(active_link_campaign_ids) == 1" in source
