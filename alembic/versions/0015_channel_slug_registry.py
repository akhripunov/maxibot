"""Preserve MAX channel identity and public slug across disconnects."""
from alembic import op
import sqlalchemy as sa

revision = "0015_channel_slug_registry"
down_revision = "0014_channel_inbox_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_slug_registry",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("max_channel_external_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("public_slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_channel_slug_registry_max_channel_external_id",
        "channel_slug_registry",
        ["max_channel_external_id"],
        unique=True,
    )
    op.create_index(
        "ix_channel_slug_registry_public_slug",
        "channel_slug_registry",
        ["public_slug"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_channel_slug_registry_public_slug", table_name="channel_slug_registry")
    op.drop_index("ix_channel_slug_registry_max_channel_external_id", table_name="channel_slug_registry")
    op.drop_table("channel_slug_registry")
