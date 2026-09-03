"""Persist the primary MAX long-poll marker across worker restarts."""

from alembic import op
import sqlalchemy as sa


revision = "0056_max_polling_checkpoint"
down_revision = "0055_granular_obs_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "max_polling_checkpoints",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("marker", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("max_polling_checkpoints")
