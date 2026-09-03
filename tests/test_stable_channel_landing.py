from pathlib import Path


ROOT = Path(__file__).parents[1]
MENU = ROOT.joinpath("app/bot/menu.py").read_text()
MIGRATION = ROOT.joinpath("alembic/versions/0042_stable_channel_landing.py").read_text()


def test_reconnect_reuses_registry_landing_public_id():
    assert "registry.landing_public_id" in MENU
    assert "stable_public_id = registry.landing_public_id" in MENU
    assert "public_id=stable_public_id" in MENU


def test_delete_preserves_existing_landing_id_in_registry():
    delete_section = MENU[MENU.index("async def delete_channel_owned"):MENU.index("async def pending_inbox_channel")]
    assert "registry.landing_public_id = landing.public_id" in delete_section


def test_migration_backfills_active_landing_ids():
    assert "UPDATE channel_slug_registry" in MIGRATION
    assert "l.public_id" in MIGRATION
