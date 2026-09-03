from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_offline_target_is_goal_identifier_not_numeric_goal_id():
    sender = (ROOT / "app/traffic/metrica_sender.py").read_text()
    client = (ROOT / "app/integrations/yandex_metrica.py").read_text()
    retry = (ROOT / "scripts/controlled_yandex_retry.py").read_text()

    assert 'ensure_action_goal_identifier(counter_id, "userDidSubscribe")' in sender
    assert 'writer.writerow([client_id or "", yclid or "", timestamp, goal_identifier])' in client
    assert 'goal_identifier = str(goal["name"])' in retry
    assert 'goal_id = int(goal["id"])' not in retry
