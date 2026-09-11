from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.bot.menu import MenuRepository, subscription_management_text, subscription_quota


def _admin(*, tariff="free", active=False):
    expiry = datetime.now(timezone.utc) + timedelta(days=30) if active else None
    return SimpleNamespace(id=1, subscription_tariff=tariff, subscription_expires_at=expiry)


@pytest.mark.parametrize(
    ("tariff", "active", "expected"),
    [
        ("free", False, (1, 0)),
        ("free", True, (3, 1)),  # legacy paid accounts without a tariff map to Start
        ("start", True, (3, 1)),
        ("pro", True, (10, 3)),
        ("pro", False, (1, 0)),  # an expired subscription falls back to Free
        ("unknown", True, (1, 0)),
    ],
)
def test_subscription_quota_for_all_tariff_states(tariff, active, expected):
    assert subscription_quota(_admin(tariff=tariff, active=active)) == expected


def test_subscription_management_displays_effective_limits_and_counts():
    text = subscription_management_text(
        _admin(tariff="pro", active=True), channel_count=7, bot_count=2
    )
    assert "Текущий тариф: Pro" in text
    assert "Подключено каналов: 7/10" in text
    assert "Подключено ботов: 2/3" in text
    assert "Ежедневные отчёты: ✅ Активно" in text


class _ScalarSequence:
    def __init__(self, *values):
        self.values = list(values)

    async def scalar(self, _query):
        return self.values.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tariff", "active", "bots", "counts", "allowed"),
    [
        ("free", False, False, (0, 0), True),
        ("free", False, False, (1, 0), False),
        ("free", False, True, (0, 0), False),
        ("start", True, False, (2, 0), True),
        ("start", True, False, (3, 0), False),
        ("start", True, True, (0, 0), True),
        ("start", True, True, (0, 1), False),
        ("pro", True, False, (9, 3), True),
        ("pro", True, False, (10, 0), False),
        ("pro", True, True, (0, 2), True),
        ("pro", True, True, (0, 3), False),
    ],
)
async def test_quota_blocks_only_at_effective_limit(tariff, active, bots, counts, allowed):
    admin = _admin(tariff=tariff, active=active)
    repo = MenuRepository(None)
    db = _ScalarSequence(*counts)
    if allowed:
        await repo._check_quota(db, admin, bots=bots)
    else:
        with pytest.raises(ValueError) as error:
            await repo._check_quota(db, admin, bots=bots)
        assert str(error.value) == (
            "SUBSCRIPTION_BOT_LIMIT" if bots else "SUBSCRIPTION_CHANNEL_LIMIT"
        )
