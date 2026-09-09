"""Store system landing attribution metadata needed for diagnostics."""
from alembic import op
import sqlalchemy as sa


revision = "0040_system_visit_metadata"
down_revision = "0039_system_yandex_owner_login"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("system_traffic_visits")}
    additions = {
        "utm_content": sa.String(length=255),
        "utm_term": sa.String(length=255),
        "metrica_client_id": sa.String(length=255),
        "referer": sa.String(length=2048),
        "user_agent": sa.Text(),
    }
    for name, column_type in additions.items():
        if name not in columns:
            op.add_column("system_traffic_visits", sa.Column(name, column_type, nullable=True))


def downgrade():
    for name in ("user_agent", "referer", "metrica_client_id", "utm_term", "utm_content"):
        op.drop_column("system_traffic_visits", name)
