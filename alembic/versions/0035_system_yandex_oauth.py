"""Store the independent system MAXIBOT Yandex OAuth connection."""
from alembic import op
import sqlalchemy as sa

revision = "0035_system_yandex_oauth"
down_revision = "0034_seed_system_promotion"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("system_promotion_profiles")}
    if "yandex_oauth_token_encrypted" not in columns:
        op.add_column("system_promotion_profiles", sa.Column("yandex_oauth_token_encrypted", sa.Text(), nullable=True))
    if "yandex_oauth_connected_at" not in columns:
        op.add_column("system_promotion_profiles", sa.Column("yandex_oauth_connected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("system_promotion_profiles", "yandex_oauth_connected_at")
    op.drop_column("system_promotion_profiles", "yandex_oauth_token_encrypted")
