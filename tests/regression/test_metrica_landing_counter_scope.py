from app.traffic.metrica_tag import metrica_tag_html, valid_counter_id


def test_valid_counter_id_is_numeric_and_positive_only():
    assert valid_counter_id("111595093") == "111595093"
    assert valid_counter_id(" 111595093 ") == "111595093"
    assert valid_counter_id("<script>") is None
    assert valid_counter_id("0") is None
    assert valid_counter_id(None) is None


def test_landing_tag_contains_only_the_channel_counter():
    tag_a = metrica_tag_html("111595093")
    tag_b = metrica_tag_html("222222222")
    assert "ym(111595093" in tag_a
    assert "111595093" not in tag_b
    assert "222222222" in tag_b
    assert "mc.yandex.ru/metrika/tag.js" in tag_a
    assert '"init", {defer:true' in tag_a
    assert 'maxibot-metrica-error' in tag_a
    assert 'maxibot-metrica-client-id' in tag_a
    assert '_mb_retry' in tag_a
    assert 'maxibotMetrikaAttempts < 2' in tag_a


def test_missing_counter_fails_closed_without_global_or_foreign_tag():
    assert metrica_tag_html(None) == ""
    assert metrica_tag_html("") == ""
    assert "ym(" not in metrica_tag_html("not-a-counter")


def test_landing_renders_profile_counter_only_when_yandex_enabled():
    from pathlib import Path

    source = (Path(__file__).parents[2] / "app" / "main.py").read_text()
    redirect = source[source.index("async def _traffic_redirect"):source.index('@app.get("/go/{public_id}"')]
    assert "profile.yandex_counter_id if profile and profile.yandex_enabled else None" in redirect
    assert "metrica_tag_html" in redirect


def test_landing_sends_metrika_hit_before_max_navigation():
    from pathlib import Path

    source = (Path(__file__).parents[2] / "app" / "main.py").read_text()
    redirect = source[source.index("async def _traffic_redirect"):source.index('@app.get("/go/{public_id}"')]
    assert "ym({json.dumps(metrica_counter)}, 'hit', window.location.href" in redirect
    assert "METRICA_HIT_CONFIRMED" in redirect
    runtime = redirect[redirect.index("trafficSignal('MAX_LINK_ASSIGNED')"):]
    assert runtime.index("if ({json.dumps(bool(metrica_counter))})") < runtime.index("'hit', window.location.href")
    assert "maxibot-metrica-error" in redirect
    assert "/traffic/metrica/client-id" in redirect
