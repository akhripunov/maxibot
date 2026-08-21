from __future__ import annotations

import pytest

from app.traffic.identity_poc import IdentityPoc


def test_yclid_to_max_user_to_join_request_chain() -> None:
    poc = IdentityPoc(signing_key=b"test-key", now=1_700_000_000)
    visit = poc.create_visit(
        yclid="TEST_YCLID",
        channel_id="test-channel",
        channel_url="https://max.ru/join/test-channel",
    )

    bound = poc.resolve_start_token(visit.start_token, max_user_id="max-user-42")
    channel_url = poc.open_channel(visit_id=bound.visit_id, max_user_id="max-user-42")
    join = poc.observe_join_request(channel_id="test-channel", max_user_id="max-user-42")

    assert visit.visitor_id
    assert visit.visit_id
    assert visit.yclid == "TEST_YCLID"
    assert bound.visit_id == visit.visit_id
    assert channel_url == visit.channel_url
    assert join.max_user_id == "max-user-42"
    assert join.visitor_id == visit.visitor_id
    assert join.visit_id == visit.visit_id
    assert join.yclid == "TEST_YCLID"


def test_start_token_is_signed_and_one_time() -> None:
    poc = IdentityPoc(signing_key=b"test-key", now=1_700_000_000)
    visit = poc.create_visit(
        yclid="TEST_YCLID",
        channel_id="test-channel",
        channel_url="https://max.ru/join/test-channel",
    )

    with pytest.raises(ValueError, match="invalid start token"):
        poc.resolve_start_token(visit.start_token[:-1] + "x", max_user_id="max-user-42")

    poc.resolve_start_token(visit.start_token, max_user_id="max-user-42")
    with pytest.raises(ValueError, match="already used"):
        poc.resolve_start_token(visit.start_token, max_user_id="max-user-42")


def test_join_request_from_another_user_is_not_attributed() -> None:
    poc = IdentityPoc(signing_key=b"test-key", now=1_700_000_000)
    visit = poc.create_visit(
        yclid="TEST_YCLID",
        channel_id="test-channel",
        channel_url="https://max.ru/join/test-channel",
    )
    poc.resolve_start_token(visit.start_token, max_user_id="max-user-42")

    with pytest.raises(ValueError, match="one exact bound visit"):
        poc.observe_join_request(channel_id="test-channel", max_user_id="other-user")
