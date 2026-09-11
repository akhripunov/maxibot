"""Add opt-in object-level execution mode for AI Promotion Autopilot V1."""
from alembic import op
import sqlalchemy as sa

revision = "0086_ads_autopilot_v1"
down_revision = "0085_bot_recipient_state_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_promotion_campaign_links", sa.Column("execution_mode", sa.String(length=16), nullable=True, server_default="ADVISORY"))
    op.add_column("ads_promotion_campaign_links", sa.Column("autopilot_config_json", sa.Text(), nullable=True, server_default="{}"))
    op.add_column("ads_promotion_campaign_links", sa.Column("execution_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE ads_promotion_campaign_links SET execution_mode = 'ADVISORY' WHERE execution_mode IS NULL")
    op.execute("UPDATE ads_promotion_campaign_links SET autopilot_config_json = '{}' WHERE autopilot_config_json IS NULL")
    op.alter_column("ads_promotion_campaign_links", "execution_mode", nullable=False, server_default="ADVISORY")
    op.alter_column("ads_promotion_campaign_links", "autopilot_config_json", nullable=False, server_default="{}")
    op.create_index("ix_ads_promotion_campaign_links_execution_mode", "ads_promotion_campaign_links", ["execution_mode"])


def downgrade() -> None:
    op.drop_index("ix_ads_promotion_campaign_links_execution_mode", table_name="ads_promotion_campaign_links")
    op.drop_column("ads_promotion_campaign_links", "execution_updated_at")
    op.drop_column("ads_promotion_campaign_links", "autopilot_config_json")
    op.drop_column("ads_promotion_campaign_links", "execution_mode")
