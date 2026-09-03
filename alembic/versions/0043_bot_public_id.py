"""Give every custom bot a stable bot-scoped public landing identifier."""
from alembic import op
import sqlalchemy as sa
import secrets

revision = "0043_bot_public_id"
down_revision = "0042_stable_channel_landing"
branch_labels = None
depends_on = None


def _public_id() -> str:
    return str(secrets.randbelow(90_000_000_000_000) + 10_000_000_000_000)


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("bot_connections")}
    if "public_id" not in columns:
        op.add_column("bot_connections", sa.Column("public_id", sa.String(length=64), nullable=True))
        op.create_index("ix_bot_connections_public_id", "bot_connections", ["public_id"], unique=True)

    rows = bind.execute(sa.text("SELECT id FROM bot_connections WHERE public_id IS NULL")).fetchall()
    used = {str(value[0]) for value in bind.execute(sa.text("SELECT public_id FROM bot_connections WHERE public_id IS NOT NULL"))}
    for (bot_id,) in rows:
        value = _public_id()
        while value in used:
            value = _public_id()
        bind.execute(
            sa.text("UPDATE bot_connections SET public_id = :public_id WHERE id = :id"),
            {"public_id": value, "id": bot_id},
        )
        used.add(value)


def downgrade():
    op.drop_index("ix_bot_connections_public_id", table_name="bot_connections")
    op.drop_column("bot_connections", "public_id")
