"""Allow one system visit to produce one conversion per goal."""
from alembic import op
import sqlalchemy as sa


revision = "0041_system_conversion_per_goal"
down_revision = "0040_system_visit_metadata"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("system_conversion_queue")}
    for name in ("ix_system_conversion_queue_visit_id",):
        if name in indexes:
            op.drop_index(name, table_name="system_conversion_queue")
    constraints = {item.get("name") for item in inspector.get_unique_constraints("system_conversion_queue")}
    if "uq_system_conversion_visit_goal" not in constraints:
        op.create_unique_constraint(
            "uq_system_conversion_visit_goal",
            "system_conversion_queue",
            ["visit_id", "goal"],
        )
    if "ix_system_conversion_queue_visit_id" not in indexes:
        op.create_index("ix_system_conversion_queue_visit_id", "system_conversion_queue", ["visit_id"])


def downgrade():
    op.drop_constraint("uq_system_conversion_visit_goal", "system_conversion_queue", type_="unique")
    op.drop_index("ix_system_conversion_queue_visit_id", table_name="system_conversion_queue")
    op.create_index("ix_system_conversion_queue_visit_id", "system_conversion_queue", ["visit_id"], unique=True)
