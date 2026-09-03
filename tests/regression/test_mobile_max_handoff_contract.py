"""Release-blocking contract for the real-device-proven MAX mobile handoff.

These tests intentionally protect the platform branches that cannot be
reliably exercised by headless browser automation.  A change here requires
fresh owner-device acceptance from both MAX and external Safari.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_SOURCE = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
MINI_SOURCE = (ROOT / "miniapp" / "traffic.html").read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


def test_mobile_landing_keeps_same_token_and_immediate_native_startapp():
    redirect = _section(MAIN_SOURCE, "async def _traffic_redirect", '@app.get("/go/{public_id}"')

    assert "build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link" in redirect
    assert "handoff_destination = link_builder(settings.max_bot_username, visit.startapp_token)" in redirect
    assert "window.location.replace" in redirect
    assert "setTimeout" not in redirect
    assert "<iframe" not in redirect
    assert "fetch(" not in redirect


def test_ios_channel_handoff_never_uses_bridge_path():
    handoff = _section(MINI_SOURCE, "function openChannel(targetUrl)", "async function resolveChannel")

    apple_guard = "const isAppleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent)"
    guarded_bridge = "if (!isAppleMobile && typeof webApp?.openMaxLink === 'function')"
    assert apple_guard in handoff
    assert guarded_bridge in handoff
    assert "if (typeof webApp?.openMaxLink === 'function')" not in handoff
    assert handoff.index(apple_guard) < handoff.index(guarded_bridge) < handoff.index("openViaAnchor(targetUrl)")


def test_ios_anchor_handoff_is_top_level_user_flow_compatible():
    anchor = _section(MINI_SOURCE, "function openViaAnchor(targetUrl)", "function openChannel(targetUrl)")

    assert "link.href = targetUrl" in anchor
    assert "link.target = '_blank'" in anchor
    assert "link.rel = 'noopener'" in anchor
    assert "link.click()" in anchor
    assert "webApp.close()" not in anchor


def test_android_and_non_apple_web_keep_their_proven_branches():
    handoff = _section(MINI_SOURCE, "function openChannel(targetUrl)", "async function resolveChannel")

    assert "if (platform === 'android')" in handoff
    assert "window.location.replace(targetUrl)" in handoff
    assert "if (!isAppleMobile && typeof webApp?.openMaxLink === 'function')" in handoff
    assert "webApp.openMaxLink(targetUrl)" in handoff


def test_miniapp_handoff_telemetry_is_best_effort_and_precedes_platform_branch():
    handoff = _section(MINI_SOURCE, "function openChannel(targetUrl)", "async function resolveChannel")

    assert "miniappSignal('MINIAPP_CHANNEL_HANDOFF_STARTED')" in handoff
    assert handoff.index("miniappSignal('MINIAPP_CHANNEL_HANDOFF_STARTED')") < handoff.index("const platform")
    assert "setTimeout" not in handoff
    assert "window.location.replace(targetUrl)" in handoff

    pre_handoff = MINI_SOURCE[: MINI_SOURCE.index("function openViaAnchor")]
    assert "keepalive: true" in pre_handoff
    assert ".catch(() => {})" in pre_handoff
    assert "catch (_) {}" in pre_handoff


def test_miniapp_resolves_identity_once_before_channel_open():
    resolve = _section(MINI_SOURCE, "async function resolveChannel", "resolveChannel().catch")

    assert "if (!initData)" in resolve
    assert "api('/api/traffic/resolve', {method: 'POST'})" in resolve
    assert "openChannel(data.target_url)" in resolve
    assert "/api/traffic/identify" not in resolve
    assert "/api/traffic/channel-open" not in resolve


def test_visual_timers_cannot_trigger_a_second_navigation():
    pre_handoff = MINI_SOURCE[: MINI_SOURCE.index("function openViaAnchor")]

    assert "slowTimer" in pre_handoff
    assert "fallbackTimer" in pre_handoff
    assert "openChannel(" not in pre_handoff
    assert "window.location" not in pre_handoff
