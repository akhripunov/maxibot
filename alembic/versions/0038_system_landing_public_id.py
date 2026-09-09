"""Give the system landing its own numeric public URL identifier."""
from alembic import op
import sqlalchemy as sa

revision = "0038_system_landing_public_id"
down_revision = "0037_system_conversion_queue"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("system_promotion_profiles")}
    if "public_id" not in columns:
        op.add_column("system_promotion_profiles", sa.Column("public_id", sa.String(64), nullable=True))
        op.create_index("ix_system_promotion_profiles_public_id", "system_promotion_profiles", ["public_id"], unique=True)
    op.execute("UPDATE system_promotion_profiles SET public_id='80988024303861', landing_url='https://go.maxibot.pro/80988024303861' WHERE system_key='maxibot' AND public_id IS NULL")

def downgrade():
    op.drop_index("ix_system_promotion_profiles_public_id", table_name="system_promotion_profiles")
    op.drop_column("system_promotion_profiles", "public_id")
