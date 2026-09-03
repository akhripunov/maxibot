from datetime import datetime, timezone

from app.bot.menu import EXTERNAL_LINKS_IN_AI_RESULT, format_ads_result


def _body(diagnosis: str, recommendation: str = "Наблюдать") -> str:
    return format_ads_result(
        headline="🟢 Анализ завершён",
        channel_title="Москва 365",
        direct_connected=True,
        campaign_name="test-campaign",
        ai_status="✅ Завершён",
        checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 10, "conversions_7d": 2, "actual_cpa_7d": 5},
        diagnosis=diagnosis,
        recommendation=recommendation,
        confidence="HIGH",
        completed=True,
    )


def test_ai_result_default_external_links_policy_is_not_allowed():
    assert EXTERNAL_LINKS_IN_AI_RESULT == "NOT_ALLOWED"


def test_ai_result_without_url_is_preserved():
    body = _body("CPA стабилен, требуется наблюдение.")
    assert "CPA стабилен, требуется наблюдение." in body


def test_dzen_url_cannot_reach_user_facing_ai_result():
    body = _body("Проверка источника https://dzen.ru/a/unsafe-article")
    assert "dzen.ru" not in body
    assert "unsafe-article" not in body
    assert "Проверка источника" in body


def test_arbitrary_https_url_and_markdown_preview_are_removed():
    body = _body("См. [исследование](https://example.com/news?id=42) и https://evil.example/a")
    assert "example.com" not in body
    assert "evil.example" not in body
    assert "исследование" in body


def test_internal_provenance_is_not_mutated_by_user_facing_sanitizer():
    provenance = "V2-abc123"
    body = _body(f"Сигнал подтверждён доказательством {provenance}; источник скрыт")
    assert provenance in body
    assert "источник скрыт" in body


def test_existing_ai_promotion_buttons_are_preserved():
    # The renderer returns text only; this assertion locks the adjacent
    # promotion screen contract that supplies the existing controls.
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert 'f"🔄 Проверить снова"' in source or '"🔄 Проверить снова"' in source
    assert 'f"ads_check:{channel.id}"' in source


def test_ai_sender_disables_preview_only_for_ai_result_edit():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index("async def _handle_ads_ai_event"):source.index("    async def handle(")]
    assert "await self.client.edit_message(" in section
    assert "disable_link_preview=True" in section


def test_ai_dashboard_render_disables_preview_for_edit_and_new_message_only():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index("    async def _render("):source.index("    @staticmethod\n    def _support_status_label")]
    assert 'ai_result_screen = screen.startswith("ads:")' in section
    assert 'edit_kwargs = {"disable_link_preview": True} if ai_result_screen else {}' in section
    assert 'await_kwargs = {"disable_link_preview": True} if ai_result_screen else {}' in section
