from pathlib import Path


def test_home_banner_asset_is_scoped_to_home_screen():
    source = Path("app/bot/menu.py").read_text()
    asset = Path("app/static/images/maxibot_home_banner_compact.webp")
    assert asset.exists()
    assert asset.stat().st_size < 300_000
    from PIL import Image
    with Image.open(asset) as image:
        width, height = image.size
        corners = [image.getpixel(point) for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    # The compact source matches the taller MAX media card and keeps safe
    # margins around the complete artwork.
    assert 1.2 <= width / height <= 1.35
    assert height >= 1000
    assert max(sum(pixel) for pixel in corners) < 120
    assert "def home_menu_banner_media" in source
    assert 'media = []' in source
    assert "maxibot_home_banner_fit.webp" in source


def test_home_media_is_temporarily_disabled_for_all_screens():
    source = Path("app/bot/menu.py").read_text()
    assert 'media = home_menu_banner_media() if screen == "home" else []' not in source
    assert 'media = []' in source


def test_promotion_connect_uses_short_exact_copy_only_on_connect_screen():
    source = Path("app/bot/menu.py").read_text()
    assert "def promotion_connect_text()" in source
    assert 'return promotion_connect_text(), buttons(("⬅️ Назад", "promotion"))' in source
    assert "Добавьте MAXIBOT в канал — после этого он" in source
    assert "автоматически появится в списке продвижения." in source
    assert 'if screen == "promotion":' in source
    assert 'return promotion_intro_text(), buttons(*rows)' in source


def test_custom_bot_refresh_state_reaches_token_probe_and_result_screens():
    runtime = Path("app/bot/runtime.py").read_text()
    menu = Path("app/bot/menu.py").read_text()
    assert 'await self.repo.set_custom_bot_state(user_id, "refresh", bot_id)' in runtime
    assert 'if custom_state in {"waiting_custom_bot_token", "refresh"}:' in runtime
    assert "pending_bot_id" in runtime
    assert "await probe.get_me()" in runtime
    assert 'custom_bot_active:{bot.id}' in runtime
    assert 'custom_bot_found:{bot.id}' in runtime
    assert 'custom_bot_activation_error' in runtime
    assert 'custom_bot_error' in runtime
    assert 'async def update_custom_bot_token' in menu

def test_valid_bot_added_without_owner_proof_is_accepted():
    source = Path("app/bot/menu.py").read_text()
    assert 'str(chat_type or "").lower() != "channel"' in source
    assert '"owner_user_id": str(initiator_user_id)' in source
    assert '"target_url": str(link)' in source


def test_existing_owner_conflict_is_rejected():
    source = Path("app/bot/menu.py").read_text()
    assert '"channel_owned_by_another_admin"' in source


def test_non_channel_payload_is_rejected():
    source = Path("app/bot/menu.py").read_text()
    section = source[source.index("def official_bot_added_channel"):source.index("class MenuRepository")]
    assert 'chat_type = chat.get("chat_type") or chat.get("type")' in section


def test_missing_or_invalid_official_link_fails_closed():
    source = Path("app/bot/menu.py").read_text()
    section = source[source.index("def official_bot_added_channel"):source.index("class MenuRepository")]
    assert 'if not valid_max_target(link):' in section


def test_safe_bot_added_diagnostics_cover_owner_and_link_contract():
    source = Path("app/bot/menu.py").read_text()
    runtime = Path("app/bot/runtime.py").read_text()
    assert "def official_bot_added_diagnostics" in source
    assert '"owner_matches_initiator"' in source
    assert '"link_valid"' in source
    assert "MAX_CHANNEL_AUTO_CONNECT_DIAGNOSTICS" in runtime
    assert "initiator_is_owner" in runtime
    assert "get_admins(update.chat_id)" in runtime
    assert "admin_read_status = exc.status_code" in runtime


def test_application_flow_is_idempotent_and_does_not_rebind_conflicts():
    source = Path("app/bot/menu.py").read_text()
    runtime = Path("app/bot/runtime.py").read_text()
    assert "select(Channel).where(Channel.external_id == str(channel_external_id)).with_for_update()" in source
    assert '"channel_owned_by_another_admin"' in source
    assert '"status": "duplicate"' in source
    assert "if not channel.bot_connection_id" in source
    assert "connect_channel_from_bot_added(update.chat_id, update.user_id, chat)" in runtime
    assert 'raw.get("type") == "channel"' in runtime


def test_group_bot_added_requires_explicit_pending_channel_and_metadata():
    runtime = Path("app/bot/runtime.py").read_text()
    menu = Path("app/bot/menu.py").read_text()
    assert 'raw.get("is_channel") is not False' in runtime
    assert 'get_chat(update.chat_id)' in runtime
    assert 'not in {"chat", "group"}' in runtime
    assert 'pending_inbox_channel(initiator)' in runtime
    assert 'len(candidates) != 0' in runtime
    assert 'def unbind_inbox_chat' in menu


def test_group_flow_does_not_require_forwarded_message():
    runtime = Path("app/bot/runtime.py").read_text()
    section = runtime[runtime.index('initiator = update.user_id'):runtime.index('async def _bind_inbox_from_callback')]
    assert "_bind_inbox_chat(initiator, pending_channel, update.chat_id)" in section
    assert "validate_forwarded_post" not in section


def test_main_menu_order_and_channel_setup_copy():
    menu = Path("app/bot/menu.py").read_text()
    services = Path("app/domain/services.py").read_text()
    expected = [
        '("📢 Подключить канал", "connect_channel")',
        '("📚 Мои каналы", "promotion")',
        '("ℹ️ О сервисе", "about")',
    ]
    section = menu[menu.index('return menu_text(len(channels)'):menu.index('if screen == "connect_channel"')]
    assert [section.index(item) for item in expected] == sorted(section.index(item) for item in expected)
    assert "if screen == \"connect_channel\":" in menu
    assert "Перешлите" not in services[services.index('NO_CHANNELS='):]


def test_main_menu_renames_promotion_button_without_changing_callback():
    menu = Path("app/bot/menu.py").read_text()
    section = menu[menu.index('return menu_text(len(channels)'):menu.index('if screen == "connect_channel"')]
    assert '("📚 Мои каналы", "promotion")' in section
    assert '("📣 Продвижение", "promotion")' not in section


def test_channel_promotion_opens_its_own_feedback_settings():
    menu = Path("app/bot/menu.py").read_text()
    start = menu.index('if screen.startswith("promotion_channel:")')
    end = menu.index('if screen.startswith("promotion_landing:")', start)
    section = menu[start:end]
    assert '("📩 Обращения / Предложка", f"channel:{channel.id}")' in section
    assert section.index('("📩 Обращения / Предложка"') < section.index('("⬅️ Назад", "promotion")')


def test_channel_delete_button_is_moved_to_promotion_channel_screen():
    menu = Path("app/bot/menu.py").read_text()
    promotion = menu[menu.index('if screen.startswith("promotion_channel:")'):menu.index('if screen.startswith("promotion_landing:")')]
    inbox = menu[menu.index('if screen.startswith("channel:") or screen == "channel"'):menu.index('if screen.startswith("inbox_connect:")')]
    assert '("🗑 Удалить канал", f"delete:{channel.id}")' in promotion
    assert promotion.index('("🗑 Удалить канал"') < promotion.index('("⬅️ Назад", "promotion")')
    assert '("🗑 Удалить канал", f"delete:{channel.id}")' not in inbox


def test_channel_deleted_routes_to_remaining_channels_or_connect_instructions():
    menu = Path("app/bot/menu.py").read_text()
    deleted = menu[menu.index('if screen.startswith("channel_deleted:")'):menu.index('if screen.startswith("channel_delete_failed:")')]
    assert 'f"✅ Канал «{title}» удалён из MAXIBOT."' in deleted
    assert 'next_button = ("📚 Мои каналы", "channels") if channels else ("📢 Подключить канал", "connect_channel")' in deleted


def test_about_screen_has_compact_maxibot_copy_and_site_link():
    menu = Path("app/bot/menu.py").read_text()
    about = menu[menu.index('if screen == "about":'):menu.index('if screen == "owner_admin":')]
    assert '"💎 **О сервисе**\\n\\n"' in about
    assert '"🤖 **MAXIBOT** — умный сервис для продвижения и управления каналами в MAX.\\n\\n"' in about
    assert '"**Наша цель:** помочь владельцам каналов находить свою аудиторию и привлекать подписчиков эффективнее.\\n\\n"' in about
    assert '"🌐 **Наш сайт:** [maxibot.pro](https://maxibot.pro)"' in about
    assert '"text": "📣 Наш MAX-канал", "type": "link", "url": MAXIBOT_CHANNEL_URL' in about
    assert '"text": "💬 Написать в поддержку", "type": "link", "url": url' in about or 'support_button()' in about
    assert '"text": "⬅️ Главное меню", "callback_data": "home"' in about


def test_connect_channel_screen_has_exact_approved_text():
    menu = Path("app/bot/menu.py").read_text()
    for line in (
        "📢 Подключение канала",
        "Чтобы подключить канал, выполните следующие шаги:",
        "1. Откройте настройки вашего канала",
        "2. Перейдите в раздел «Подписчики»",
        "3. Нажмите «Добавить подписчиков»",
        "4. Найдите в поиске «MAXIBOT» и добавьте",
        "5. Перейдите в раздел «Администраторы» и добавьте «MAXIBOT» в администраторы канала",
    ):
        assert line in menu
    assert "Перейдите в раздел «Подписатели»" not in menu


def test_all_channel_connect_screens_use_one_helper():
    menu = Path("app/bot/menu.py").read_text()
    assert "def channel_connection_instructions()" in menu
    assert menu.count("channel_connection_instructions()") >= 5
    for stale in ("Перешлите", "TeleGraphyx", "После добавления канал автоматически"):
        assert stale not in menu


def test_channel_add_buttons_have_exact_label_and_legacy_callbacks():
    menu = Path("app/bot/menu.py").read_text()
    assert '("📣 Подключить ещё канал", "promotion_connect")' in menu
    assert '("📣 Подключить ещё", "add_channel")' in menu
    assert "➕ Добавить канал" not in menu
    assert "➕ Добавить ещё" not in menu


def test_my_bots_button_is_only_on_promotion_list_after_channel_connect_button():
    menu = Path("app/bot/menu.py").read_text()
    home = menu[menu.index('return menu_text(len(channels)'):menu.index('if screen == "connect_channel"')]
    promotion = menu[menu.index('if screen == "promotion":'):menu.index('if screen == "promotion_connect":')]
    assert '("🤖 Мои боты", "my_bots")' not in home
    assert promotion.index('(\"📣 Подключить ещё канал\"') < promotion.index('(\"🤖 Добавить бота\"')
    assert '("🤖 Добавить бота", "connect_bot")' in promotion
    assert '("⬅️ Главное меню", "home")' in promotion


def test_promotion_channel_rows_show_only_the_channel_name():
    menu = Path("app/bot/menu.py").read_text()
    assert '[(channel.title, f"promotion_channel:{channel.id}")]' in menu
    assert 'f"📢 {channel.title}"' not in menu


def test_promotion_selection_uses_exact_approved_copy():
    menu = Path("app/bot/menu.py").read_text()
    assert "def promotion_intro_text()" in menu
    for line in (
        "📖 **Как запустить рекламу**",
        "Выберите нужный канал из списка ниже.",
        "Нажмите кнопку «Яндекс.Директ».",
        "Авторизуйтесь в аккаунте Яндекс и разрешите доступ.",
        "Вставьте ссылку на лендинг в поле «Ссылка на сайт».",
        "Выберите счётчик Яндекс.Метрики, созданный MAXIBOT для этого канала, и конверсию **`userDidSubscribe`**.",
        "8. Добавьте к ссылке UTM-метку:",
        "utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_name_lat}.{campaign_id}.{gbid}&utm_content={creative_id}.{ad_id}.{retargeting_id}&utm_term={keyword}",
        "Выберите канал для продвижения:",
    ):
        assert line in menu
    assert '"type": "clipboard"' not in menu
    assert '"text": "📋 Скопировать UTM-метку"' not in menu
    assert 'PROMOTION_VIDEO_URL = f"{settings.traffic_public_base_url.rstrip(\'/\')}/video-instruction"' in menu
    assert 'f"🎥 [Видеоинструкция по настройке рекламы]({PROMOTION_VIDEO_URL})\\n\\n"' in menu
    assert menu.count("promotion_intro_text()") >= 2
