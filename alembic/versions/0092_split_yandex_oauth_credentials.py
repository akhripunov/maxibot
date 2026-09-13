"""Split Yandex Metrica and Direct OAuth credentials."""

from alembic import op
import sqlalchemy as sa

revision = "0092_split_yandex_oauth_credentials"
down_revision = "0091_generic_promotion_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        ("yandex_metrica_oauth_token_encrypted", sa.Text()),
        ("yandex_metrica_oauth_connected_at", sa.DateTime(timezone=True)),
        ("yandex_metrica_oauth_client_fingerprint", sa.String(length=64)),
        ("yandex_metrica_oauth_scopes", sa.Text()),
        ("yandex_direct_oauth_token_encrypted", sa.Text()),
        ("yandex_direct_oauth_connected_at", sa.DateTime(timezone=True)),
        ("yandex_direct_oauth_client_fingerprint", sa.String(length=64)),
        ("yandex_direct_oauth_scopes", sa.Text()),
    )
    for name, column_type in columns:
        op.add_column("traffic_profiles", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name in (
        "yandex_direct_oauth_scopes", "yandex_direct_oauth_client_fingerprint",
        "yandex_direct_oauth_connected_at", "yandex_direct_oauth_token_encrypted",
        "yandex_metrica_oauth_scopes", "yandex_metrica_oauth_client_fingerprint",
        "yandex_metrica_oauth_connected_at", "yandex_metrica_oauth_token_encrypted",
    ):
        op.drop_column("traffic_profiles", name)
