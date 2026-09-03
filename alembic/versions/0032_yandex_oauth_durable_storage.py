"""Persist encrypted per-channel Yandex OAuth connections.

Revision ID: 0032_oauth_durable_storage
Revises: 0031_join_intents
"""
from alembic import op
import sqlalchemy as sa


revision = "0032_oauth_durable_storage"
down_revision = "0031_join_intents"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("traffic_profiles")}
    if "yandex_oauth_token_encrypted" not in columns:
        op.add_column("traffic_profiles", sa.Column("yandex_oauth_token_encrypted", sa.Text(), nullable=True))
    if "yandex_oauth_connected_at" not in columns:
        op.add_column("traffic_profiles", sa.Column("yandex_oauth_connected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("traffic_profiles", "yandex_oauth_connected_at")
    op.drop_column("traffic_profiles", "yandex_oauth_token_encrypted")
