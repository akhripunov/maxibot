"""Allow one visit/membership to produce separate subscribe/unsubscribe goals."""

from alembic import op

revision = "0084_offline_conversion_goal_unique_v1"
down_revision = "0083_subscription_entitlements_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("offline_conversion_queue_visit_id_membership_id_target_key", "offline_conversion_queue", type_="unique")
    op.create_unique_constraint(
        "uq_offline_conversion_visit_membership_target_goal",
        "offline_conversion_queue",
        ["visit_id", "membership_id", "target", "goal"],
    )


def downgrade():
    op.drop_constraint("uq_offline_conversion_visit_membership_target_goal", "offline_conversion_queue", type_="unique")
    op.create_unique_constraint(
        "offline_conversion_queue_visit_id_membership_id_target_key",
        "offline_conversion_queue",
        ["visit_id", "membership_id", "target"],
    )
