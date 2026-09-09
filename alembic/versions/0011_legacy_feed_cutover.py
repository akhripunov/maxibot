"""Stop re-rendering the pre-cutover native feed history."""
from alembic import op

revision = "0011_legacy_feed_cutover"
down_revision = "0010_native_admin_feed"
branch_labels = None
depends_on = None


def upgrade():
    # Everything already in the database before this cutover is legacy.
    # New incoming rows keep the model default FALSE and are delivered once.
    op.execute(
        "UPDATE messages SET admin_feed_delivered = TRUE "
        "WHERE direction = 'in' AND admin_feed_delivered = FALSE"
    )


def downgrade():
    pass
