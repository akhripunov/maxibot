"""Store the conversion goal independently from its delivery target."""
from alembic import op
import sqlalchemy as sa

revision = "0028_offline_conversion_goal"
down_revision = "0027_channel_max_connection_kind"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("offline_conversion_queue")}
    if "goal" not in columns:
        op.add_column(
            "offline_conversion_queue",
            sa.Column("goal", sa.String(64), nullable=False, server_default="userDidSubscribe"),
        )


def downgrade():
    op.drop_column("offline_conversion_queue", "goal")
