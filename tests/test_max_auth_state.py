import json

import pytest

from app.integrations.max_auth_state import MaxAuthState, MaxAuthStateError, PersistedMaxAuth


def test_auth_state_round_trip_is_minimal_and_private(tmp_path):
    path = tmp_path / "max-auth-state.json"
    MaxAuthState({"Origin": "https://web.max.ru", "Cookie": "redacted-test"}).save(path)
    loaded = MaxAuthState.load(path)
    assert loaded.headers["Origin"] == "https://web.max.ru"
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_persisted_auth_provider_returns_copy(tmp_path):
    path = tmp_path / "auth.json"
    MaxAuthState({"X-Test": "value"}).save(path)
    provider = PersistedMaxAuth(MaxAuthState.load(path))
    headers = await provider.get_auth_headers()
    headers["X-Test"] = "changed"
    assert (await provider.get_auth_headers())["X-Test"] == "value"


def test_missing_or_invalid_auth_state_is_expired_or_invalid(tmp_path):
    with pytest.raises(MaxAuthStateError, match="MAX_AUTH_EXPIRED"):
        MaxAuthState.load(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"headers": {}}))
    with pytest.raises(MaxAuthStateError, match="MAX_AUTH_STATE_INVALID"):
        MaxAuthState.load(invalid)
