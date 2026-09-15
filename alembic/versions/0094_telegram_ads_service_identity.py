"""Add shared encrypted Telegram Ads service identity storage.

Revision ID: 0094_telegram_ads_service_identity
Revises: 0093_experiment_evidence_policy_v1
"""
from alembic import op
import sqlalchemy as sa

revision = "0094_telegram_ads_service_identity"
down_revision = "0093_experiment_evidence_policy_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing 0046 uniqueness was provider-only.  It prevents one tenant
    # from binding two Telegram Organizations (and is not needed once the
    # durable external account identity is part of the key).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ads_accounts", recreate="always") as batch:
            batch.drop_constraint("uq_ads_account_provider", type_="unique")
            batch.create_unique_constraint("uq_ads_account_provider_external", ["admin_id", "provider", "external_account_id"])
    else:
        op.drop_constraint("uq_ads_account_provider", "ads_accounts", type_="unique")
        op.create_unique_constraint("uq_ads_account_provider_external", "ads_accounts", ["admin_id", "provider", "external_account_id"])
    op.create_table(
        "telegram_ads_service_identities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("identity_key", sa.String(length=128), nullable=False),
        sa.Column("encrypted_session", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="CONNECTED"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_owner_assertion", sa.String(length=255), nullable=True),
        sa.Column("session_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("identity_key", name="uq_telegram_ads_service_identity_key"),
    )
    op.create_index("ix_telegram_ads_service_identities_identity_key", "telegram_ads_service_identities", ["identity_key"], unique=True)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ads_accounts", recreate="always") as batch:
            batch.add_column(sa.Column("telegram_service_identity_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key("fk_ads_accounts_telegram_service_identity", "telegram_ads_service_identities", ["telegram_service_identity_id"], ["id"], ondelete="SET NULL")
    else:
        op.add_column("ads_accounts", sa.Column("telegram_service_identity_id", sa.String(length=36), nullable=True))
        op.create_foreign_key("fk_ads_accounts_telegram_service_identity", "ads_accounts", "telegram_ads_service_identities", ["telegram_service_identity_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_ads_accounts_telegram_service_identity_id", "ads_accounts", ["telegram_service_identity_id"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_ads_accounts_telegram_service_identity_id", table_name="ads_accounts")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ads_accounts", recreate="always") as batch:
            batch.drop_constraint("fk_ads_accounts_telegram_service_identity", type_="foreignkey")
            batch.drop_column("telegram_service_identity_id")
    else:
        op.drop_constraint("fk_ads_accounts_telegram_service_identity", "ads_accounts", type_="foreignkey")
        op.drop_column("ads_accounts", "telegram_service_identity_id")
    op.drop_index("ix_telegram_ads_service_identities_identity_key", table_name="telegram_ads_service_identities")
    op.drop_table("telegram_ads_service_identities")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ads_accounts", recreate="always") as batch:
            batch.drop_constraint("uq_ads_account_provider_external", type_="unique")
            batch.create_unique_constraint("uq_ads_account_provider", ["admin_id", "provider"])
    else:
        op.drop_constraint("uq_ads_account_provider_external", "ads_accounts", type_="unique")
        op.create_unique_constraint("uq_ads_account_provider", "ads_accounts", ["admin_id", "provider"])
