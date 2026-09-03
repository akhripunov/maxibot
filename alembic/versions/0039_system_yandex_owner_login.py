"""Store the non-secret Yandex account login for system promotion."""
from alembic import op
import sqlalchemy as sa


revision = "0039_system_yandex_owner_login"
down_revision = "0038_system_landing_public_id"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("system_promotion_profiles")}
    if "yandex_owner_login" not in columns:
        op.add_column("system_promotion_profiles", sa.Column("yandex_owner_login", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("system_promotion_profiles", "yandex_owner_login")
