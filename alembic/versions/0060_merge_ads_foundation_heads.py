"""Merge the independent 0058 migration branches.

This revision intentionally performs no schema or data operations.  It only
restores a single Alembic head after the diagnostics and Metrica migrations
were created independently from 0057.
"""

revision = "0060_merge_ads_foundation_heads"
down_revision = ("0059_ads_ai_worker_release", "0058_metrica_upload_tracking")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
