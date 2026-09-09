"""Create the disabled system-scoped MAXIBOT promotion profile."""
from alembic import op


revision = "0034_seed_system_promotion"
down_revision = "0033_system_promotion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "INSERT INTO system_promotion_profiles "
        "(id, system_key, enabled, created_at, updated_at) "
        "VALUES ('system-maxibot', 'maxibot', false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
        "ON CONFLICT (system_key) DO NOTHING"
    )


def downgrade():
    op.execute("DELETE FROM system_promotion_profiles WHERE system_key = 'maxibot'")
