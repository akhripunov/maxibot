from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_owner_miniapp_state_is_scoped_to_current_admin():
    source = (ROOT / "app" / "main.py").read_text()
    route = source[source.index('@app.get("/api/miniapp/owner-state")'):]
    route = route[:route.index('@app.get("/api/dialogs")')]

    assert "admin: Admin = Depends(require_admin)" in route
    assert "Channel.admin_id == admin.id" in route
    assert '"channels"' in route
    assert '"connect_instructions"' in route
    assert "Подписчики" in route
    assert "Добавить подписчиков" in route
    assert "МАКСИБОТ" in route
    assert "Администраторы" in route
    assert "admin.id" not in route.split('return {', 1)[1].split('"channels"', 1)[0]


def test_owner_miniapp_has_empty_and_connected_channel_states():
    index = (ROOT / "miniapp" / "index.html").read_text()
    app = (ROOT / "miniapp" / "app.js").read_text()
    css = (ROOT / "miniapp" / "owner.css").read_text()

    assert 'class="owner-home-mode"' in index
    assert 'id="owner-home"' in index
    assert "/api/miniapp/owner-state" in app
    assert "Ваши проекты" in index
    assert "Выберите свой канал, группу или бота, чтобы посмотреть аналитику." in index
    assert "Каналов пока нет" not in app
    assert "Канал подключён" in app
    assert "data-owner-channel" in app
    assert "unread" not in app[app.index("function renderOwnerState"):app.index("async function openOwnerHome")]
    assert "owner-home-mode" in css
    assert "background: #fff" in css
    assert "color-scheme: light" in css
    assert ".owner-channel-avatar img" in css


def test_owner_miniapp_does_not_replace_traffic_miniapp_contract():
    main = (ROOT / "app" / "main.py").read_text()
    traffic = (ROOT / "miniapp" / "traffic.html").read_text()
    mini = main[main.index('@app.get("/mini", response_class=FileResponse)'):main.index('@app.get("/traffic-test/app"')]
    assert 'if not startapp:' in mini
    assert 'FileResponse(mini_dir / "index.html"' in mini
    assert 'FileResponse(mini_dir / "traffic.html"' in mini
    assert "/api/traffic/resolve" in traffic
    assert "startapp" in traffic
    assert "channel" in traffic


def test_owner_miniapp_analytics_is_project_scoped_and_shared():
    main = (ROOT / "app" / "main.py").read_text()
    menu = (ROOT / "app" / "bot" / "menu.py").read_text()
    app = (ROOT / "miniapp" / "app.js").read_text()
    assert '@app.get("/api/miniapp/analytics/{project_type}/{project_id}")' in main
    assert 'Channel.id == project_id, Channel.admin_id == admin.id' in main
    assert 'BotConnection.id == project_id, BotConnection.owner_admin_id == admin.id' in main
    assert 'Channel.bot_connection_id == project.id' in main
    assert 'traffic_stats_for_channels' in main
    assert 'traffic_stats_for_channels' in menu
    assert '/api/miniapp/analytics/' in app
    assert 'MAX_OPEN_LIKELY' in app
    assert 'EXACT' in app
