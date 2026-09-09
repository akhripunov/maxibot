"""Add provider-neutral CHANNEL/BOT campaign links and migrate scope."""

from alembic import op
import sqlalchemy as sa


revision = "0074_ads_provider_neutral_links"
down_revision = "0073_free_trial_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ads_promotion_campaign_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("channel_id", sa.String(length=36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=True),
        sa.Column("bot_promotion_profile_id", sa.String(length=36), sa.ForeignKey("bot_promotion_profiles.id", ondelete="CASCADE"), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(object_type = 'CHANNEL' AND channel_id IS NOT NULL AND bot_promotion_profile_id IS NULL) OR "
            "(object_type = 'BOT' AND channel_id IS NULL AND bot_promotion_profile_id IS NOT NULL)",
            name="ck_ads_promotion_campaign_link_one_object",
        ),
    )
    op.create_index("ix_ads_promotion_campaign_links_object_type", "ads_promotion_campaign_links", ["object_type"])
    op.create_index("ix_ads_promotion_campaign_links_channel_id", "ads_promotion_campaign_links", ["channel_id"])
    op.create_index("ix_ads_promotion_campaign_links_bot_profile_id", "ads_promotion_campaign_links", ["bot_promotion_profile_id"])
    op.create_index("ix_ads_promotion_campaign_links_campaign_id", "ads_promotion_campaign_links", ["campaign_id"])
    op.create_index("ix_ads_promotion_campaign_links_status", "ads_promotion_campaign_links", ["status"])
    op.create_index(
        "uq_ads_promotion_campaign_channel",
        "ads_promotion_campaign_links",
        ["channel_id", "campaign_id"],
        unique=True,
        postgresql_where=sa.text("object_type = 'CHANNEL'"),
    )
    op.create_index(
        "uq_ads_promotion_campaign_bot",
        "ads_promotion_campaign_links",
        ["bot_promotion_profile_id", "campaign_id"],
        unique=True,
        postgresql_where=sa.text("object_type = 'BOT'"),
    )
    op.alter_column("ads_snapshots", "channel_id", nullable=True)

    # The legacy table remains for compatibility.  Preserve every existing
    # row and status; this copy does not activate an inactive legacy link.
    op.execute(sa.text(
        """
        INSERT INTO ads_promotion_campaign_links
            (id, object_type, channel_id, bot_promotion_profile_id,
             campaign_id, status, created_at, updated_at)
        SELECT id, 'CHANNEL', channel_id, NULL, campaign_id, status,
               COALESCE(created_at, CURRENT_TIMESTAMP),
               COALESCE(created_at, CURRENT_TIMESTAMP)
        FROM ads_channel_campaign_links
        ON CONFLICT (id) DO NOTHING
        """
    ))

    # Link MaxiBot by exact provider identity, never by campaign name.
    op.execute(sa.text(
        """
        INSERT INTO ads_promotion_campaign_links
            (id, object_type, channel_id, bot_promotion_profile_id,
             campaign_id, status, created_at, updated_at)
        SELECT md5('ads-bot-link:maxibot:' || bp.id || ':' || c.id),
               'BOT', NULL, bp.id, c.id, 'ACTIVE',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM bot_promotion_profiles bp
        JOIN ads_campaigns c
          ON c.external_id = '713929699'
        JOIN ads_accounts a
          ON a.id = c.account_id
         AND a.provider = 'YANDEX_DIRECT'
         AND a.status = 'CONNECTED'
        WHERE bp.bot_key = 'maxibot'
          AND bp.bot_type = 'SYSTEM'
          AND bp.enabled = TRUE
          AND c.state <> 'ARCHIVED'
          AND NOT EXISTS (
              SELECT 1
              FROM ads_promotion_campaign_links existing
              WHERE existing.object_type = 'BOT'
                AND existing.bot_promotion_profile_id = bp.id
                AND existing.campaign_id = c.id
          )
        """
    ))


def downgrade() -> None:
    op.alter_column("ads_snapshots", "channel_id", nullable=False)
    op.drop_index("uq_ads_promotion_campaign_bot", table_name="ads_promotion_campaign_links")
    op.drop_index("uq_ads_promotion_campaign_channel", table_name="ads_promotion_campaign_links")
    op.drop_index("ix_ads_promotion_campaign_links_status", table_name="ads_promotion_campaign_links")
    op.drop_index("ix_ads_promotion_campaign_links_campaign_id", table_name="ads_promotion_campaign_links")
    op.drop_index("ix_ads_promotion_campaign_links_bot_profile_id", table_name="ads_promotion_campaign_links")
    op.drop_index("ix_ads_promotion_campaign_links_channel_id", table_name="ads_promotion_campaign_links")
    op.drop_index("ix_ads_promotion_campaign_links_object_type", table_name="ads_promotion_campaign_links")
    op.drop_table("ads_promotion_campaign_links")
