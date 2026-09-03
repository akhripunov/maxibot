from pathlib import Path


ROOT = Path(__file__).parents[1]
MENU = ROOT.joinpath("app/bot/menu.py").read_text()
MODELS = ROOT.joinpath("app/db/models.py").read_text()
MIGRATION = ROOT.joinpath("alembic/versions/0043_bot_public_id.py").read_text()


def test_bot_has_independent_stable_public_id():
    section = MODELS[MODELS.index("class BotConnection"):MODELS.index("class CustomBotMessage")]
    assert "public_id" in section
    assert "unique=True" in section
    assert "public_id=new_landing_public_id()" in MENU


def test_bot_public_id_migration_backfills_existing_rows():
    assert '"bot_connections"' in MIGRATION
    assert "WHERE public_id IS NULL" in MIGRATION
    assert "UPDATE bot_connections SET public_id" in MIGRATION


def test_bot_link_builder_is_separate_named_api():
    assert "def bot_landing_url(public_id: str)" in MENU
