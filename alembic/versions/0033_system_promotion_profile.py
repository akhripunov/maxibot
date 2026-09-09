"""Add independent system-scoped MAXIBOT promotion configuration."""
from alembic import op
import sqlalchemy as sa


revision = "0033_system_promotion"
down_revision = "0032_oauth_durable_storage"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "system_promotion_profiles" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "system_promotion_profiles",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("system_key", sa.String(length=64), nullable=False, unique=True),
            sa.Column("landing_url", sa.String(length=2048), nullable=True),
            sa.Column("target_url", sa.String(length=2048), nullable=True),
            sa.Column("yandex_counter_id", sa.String(length=128), nullable=True),
            sa.Column("yandex_goal_name", sa.String(length=128), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    op.drop_table("system_promotion_profiles")
