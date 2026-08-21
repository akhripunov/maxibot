"""Make the primary MAX client an explicit channel connection."""
from alembic import op
import sqlalchemy as sa

revision = "0027_channel_max_connection_kind"
down_revision = "0026_max_chat_metadata"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("channels")}
    if "max_connection_kind" not in columns:
        op.add_column(
            "channels",
            sa.Column("max_connection_kind", sa.String(24), nullable=False,
                      server_default="PRIMARY"),
        )
    # Preserve the existing custom-bot contract during the migration.
    op.execute(
        "UPDATE channels SET max_connection_kind = 'BOT_CONNECTION' "
        "WHERE bot_connection_id IS NOT NULL"
    )


def downgrade():
    op.drop_column("channels", "max_connection_kind")
