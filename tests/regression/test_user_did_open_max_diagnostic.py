from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_open_max_diagnostic_goal_is_distinct_from_subscription_goal():
    source = (ROOT / "app" / "integrations" / "yandex_metrica.py").read_text()
    assert '"user_open_max_goal_id", "userDidOpenMax"' in source
    assert '"user_subscribe_goal_id", "userDidSubscribe"' in source


def test_miniapp_reports_open_max_only_after_verified_resolve_response():
    source = (ROOT / "miniapp" / "traffic.html").read_text()
    assert "reportMaxOpened(data.metrica_counter_id);" in source
    assert "reachGoal', 'userDidOpenMax'" in source
    assert "openChannel(data.target_url);" in source
    assert source.index("reportMaxOpened(data.metrica_counter_id);") < source.index("openChannel(data.target_url);")


def test_open_max_goal_does_not_replace_subscription_conversion():
    source = (ROOT / "app" / "traffic" / "metrica_sender.py").read_text()
    assert '"userDidSubscribe"' in source
    assert "userDidOpenMax" not in source
