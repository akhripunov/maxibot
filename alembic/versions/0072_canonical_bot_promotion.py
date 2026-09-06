"""Create the canonical BOT promotion configuration domain.

The legacy system promotion table is retained for rollback/landing
compatibility.  Promotion reads and new system OAuth writes move to the
canonical BOT table; no traffic, identity, conversion, or channel rows are
rewritten.
"""

from alembic import op
import sqlalchemy as sa


revision = "0072_canonical_bot_promotion"
down_revision = "0071_bot_paid_dedupe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_promotion_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("bot_key", sa.String(length=128), nullable=False),
        sa.Column("bot_type", sa.String(length=16), nullable=False, server_default="CUSTOMER"),
        sa.Column("owner_admin_id", sa.String(length=36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=True),
        sa.Column("bot_connection_id", sa.String(length=36), sa.ForeignKey("bot_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("legacy_system_key", sa.String(length=64), nullable=True),
        sa.Column("public_id", sa.String(length=64), nullable=True),
        sa.Column("landing_url", sa.String(length=2048), nullable=True),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("yandex_counter_id", sa.String(length=128), nullable=True),
        sa.Column("yandex_goal_name", sa.String(length=128), nullable=True),
        sa.Column("yandex_owner_login", sa.String(length=255), nullable=True),
        sa.Column("yandex_oauth_token_encrypted", sa.Text(), nullable=True),
        sa.Column("yandex_oauth_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bot_key", name="uq_bot_promotion_profiles_bot_key"),
        sa.UniqueConstraint("bot_connection_id", name="uq_bot_promotion_profiles_connection"),
        sa.UniqueConstraint("legacy_system_key", name="uq_bot_promotion_profiles_legacy_system"),
        sa.UniqueConstraint("public_id", name="uq_bot_promotion_profiles_public_id"),
    )
    op.create_index("ix_bot_promotion_profiles_bot_type", "bot_promotion_profiles", ["bot_type"])
    op.create_index("ix_bot_promotion_profiles_owner_admin_id", "bot_promotion_profiles", ["owner_admin_id"])

    # Preserve the system promotion identity and its live landing URL.  This
    # is a copy, never a destructive move; old landing reads remain valid.
    op.execute(sa.text(
        """
        INSERT INTO bot_promotion_profiles (
            id, bot_key, bot_type, legacy_system_key, public_id, landing_url,
            target_url, yandex_counter_id, yandex_goal_name, yandex_owner_login,
            yandex_oauth_token_encrypted, yandex_oauth_connected_at, enabled,
            created_at, updated_at
        )
        SELECT id, 'maxibot', 'SYSTEM', system_key, public_id, landing_url,
               target_url, yandex_counter_id, yandex_goal_name, yandex_owner_login,
               yandex_oauth_token_encrypted, yandex_oauth_connected_at, enabled,
               created_at, updated_at
        FROM system_promotion_profiles
        WHERE system_key = 'maxibot'
        """
    ))

    # Create canonical identities for existing customer connections.  Their
    # old channel-derived promotion values are intentionally not copied: they
    # were not bot-scoped and must not leak across bots.
    op.execute(sa.text(
        """
        INSERT INTO bot_promotion_profiles (
            id, bot_key, bot_type, owner_admin_id, bot_connection_id,
            enabled, created_at, updated_at
        )
        SELECT md5('bot-promotion:' || bc.id), bc.max_bot_id, 'CUSTOMER',
               bc.owner_admin_id, bc.id, TRUE, bc.created_at, bc.updated_at
        FROM bot_connections bc
        WHERE NOT EXISTS (
            SELECT 1 FROM bot_promotion_profiles bp
            WHERE bp.bot_connection_id = bc.id OR bp.bot_key = bc.max_bot_id
        )
        """
    ))


def downgrade() -> None:
    op.drop_index("ix_bot_promotion_profiles_owner_admin_id", table_name="bot_promotion_profiles")
    op.drop_index("ix_bot_promotion_profiles_bot_type", table_name="bot_promotion_profiles")
    op.drop_table("bot_promotion_profiles")
