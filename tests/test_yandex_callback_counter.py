from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()


def test_yandex_callback_persists_only_counter_id_for_its_channel():
    callback = SOURCE[SOURCE.index("async def yandex_oauth_callback"):SOURCE.index("async def _landing_page")]
    assert "metrica_setup = await ensure_channel_setup(flow[\"admin\"], flow[\"channel\"], encrypted, channel.title)" in callback
    assert 'profile.yandex_counter_id = str(metrica_setup["counter_id"])' in callback
    assert "TrafficProfile.channel_id == flow[\"channel\"]" in callback
