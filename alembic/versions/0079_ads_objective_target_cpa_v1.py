"""Store the MaxiBot owner target CPA on promotion campaign links.

The provider campaign target fields are intentionally not reused: this is an
internal business objective for one CHANNEL/BOT promotion link.
"""
from alembic import op
import sqlalchemy as sa

revision = "0079_ads_objective_target_cpa_v1"
down_revision = "0078_ads_bot_ai_support_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_type", sa.String(length=32), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("target_cpa", sa.Numeric(20, 6), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("currency", sa.String(length=8), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_status", sa.String(length=16), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("objective_updated_at", "objective_status", "currency", "target_cpa", "objective_type"):
        op.drop_column("ads_promotion_campaign_links", name)
