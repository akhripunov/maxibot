"""Keep a channel landing public id stable across disconnect/reconnect."""
from alembic import op
import sqlalchemy as sa

revision = "0042_stable_channel_landing"
down_revision = "0041_system_conversion_per_goal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "channel_slug_registry",
        sa.Column("landing_public_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_channel_slug_registry_landing_public_id",
        "channel_slug_registry",
        ["landing_public_id"],
        unique=True,
    )
    # Preserve currently active URLs before any later disconnect.
    op.execute(sa.text("""
        UPDATE channel_slug_registry AS r
        SET landing_public_id = l.public_id
        FROM channels AS c
        JOIN landing_pages AS l ON l.channel_id = c.id AND l.is_active = TRUE
        WHERE r.max_channel_external_id = c.external_id
          AND r.landing_public_id IS NULL
    """))


def downgrade():
    op.drop_index("ix_channel_slug_registry_landing_public_id", table_name="channel_slug_registry")
    op.drop_column("channel_slug_registry", "landing_public_id")
