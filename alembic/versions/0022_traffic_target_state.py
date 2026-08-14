"""Store the temporary traffic target editing state on the admin."""
from alembic import op
import sqlalchemy as sa

revision = "0022_traffic_target_state"
down_revision = "0021_traffic_attribution"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("admins", sa.Column("pending_traffic_channel_id", sa.String(36)))

def downgrade():
    op.drop_column("admins", "pending_traffic_channel_id")
