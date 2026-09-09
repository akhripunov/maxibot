"""Use opaque URL-safe payloads for channel deep links."""
from alembic import op

revision = "0003_opaque_channel_slugs"
down_revision = "0002_runtime_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE channels SET public_slug = substr(md5(id || clock_timestamp()::text), 1, 12)")


def downgrade():
    # Previous title-derived slugs cannot be reconstructed safely.
    pass
