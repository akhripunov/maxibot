"""Add explicit AdsAccount ownership and regional execution metadata.

The columns are additive and nullable on historical operational rows until a
deterministic ownership validator proves they can be constrained safely.
"""
from alembic import op
import sqlalchemy as sa

revision = "0095_ads_account_regional_ownership_v1"
down_revision = "0094_telegram_ads_service_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def add_owned_fk(table: str, column: str) -> None:
        column_def = sa.Column(column, sa.String(length=36), nullable=True)
        if bind.dialect.name == "sqlite":
            # SQLite cannot add a standalone FK constraint. The rehearsal
            # validates columns/backfill; PostgreSQL receives the real FK.
            op.add_column(table, column_def)
        else:
            op.add_column(table, column_def)
            op.create_foreign_key(
                f"fk_{table}_ads_account", table, "ads_accounts", [column], ["id"], ondelete="SET NULL"
            )

    op.add_column("ads_accounts", sa.Column("storage_region", sa.String(length=16), nullable=True, server_default="RU"))
    op.add_column("ads_accounts", sa.Column("execution_region", sa.String(length=16), nullable=True, server_default="RU"))
    op.execute(sa.text("UPDATE ads_accounts SET storage_region = 'RU' WHERE storage_region IS NULL"))
    op.execute(sa.text("UPDATE ads_accounts SET execution_region = 'RU' WHERE execution_region IS NULL"))
    op.create_index("ix_ads_accounts_storage_region", "ads_accounts", ["storage_region"])
    op.create_index("ix_ads_accounts_execution_region", "ads_accounts", ["execution_region"])

    add_owned_fk("ads_report_runs", "ads_account_id")
    op.create_index("ix_ads_report_runs_ads_account_id", "ads_report_runs", ["ads_account_id"])
    # Only an exactly unique campaign/account match is backfilled.
    op.execute(sa.text("""
        UPDATE ads_report_runs AS r
        SET ads_account_id = c.account_id
        FROM ads_campaigns AS c
        WHERE r.ads_account_id IS NULL
          AND r.campaign_provider_id = c.external_id
          AND NOT EXISTS (
              SELECT 1 FROM ads_campaigns c2
              WHERE c2.external_id = r.campaign_provider_id
                AND c2.account_id <> c.account_id
          )
    """))

    add_owned_fk("ads_collection_runs", "ads_account_id")
    op.create_index("ix_ads_collection_runs_ads_account_id", "ads_collection_runs", ["ads_account_id"])
    op.execute(sa.text("UPDATE ads_collection_runs SET ads_account_id = account_id WHERE ads_account_id IS NULL AND account_id IN (SELECT id FROM ads_accounts)"))
    op.execute(sa.text("""
        UPDATE ads_collection_runs AS r
        SET ads_account_id = c.account_id
        FROM ads_campaigns AS c
        WHERE r.ads_account_id IS NULL AND r.campaign_id = c.id
    """))

    add_owned_fk("ads_data_health", "ads_account_id")
    op.create_index("ix_ads_data_health_ads_account_id", "ads_data_health", ["ads_account_id"])
    op.execute(sa.text("UPDATE ads_data_health SET ads_account_id = promotion_object_id WHERE ads_account_id IS NULL AND promotion_object_type = 'ACCOUNT' AND promotion_object_id IN (SELECT id FROM ads_accounts)"))
    # Channel health is backfilled only when the admin owns one account; an
    # ambiguous admin remains NULL and therefore fail-closed.
    op.execute(sa.text("""
        UPDATE ads_data_health AS h
        SET ads_account_id = a.id
        FROM channels ch
        JOIN ads_accounts a ON a.admin_id = ch.admin_id
        WHERE h.ads_account_id IS NULL AND h.channel_id = ch.id
          AND (SELECT count(*) FROM ads_accounts a2 WHERE a2.admin_id = ch.admin_id) = 1
    """))

    # Never silently accept an ambiguous historical mapping.  Internal
    # housekeeping runs are intentionally excluded; provider collection runs
    # and account-scoped health must be owned before the migration succeeds.
    unresolved_reports = bind.execute(sa.text("SELECT count(*) FROM ads_report_runs WHERE ads_account_id IS NULL")).scalar_one()
    unresolved_runs = bind.execute(sa.text("SELECT count(*) FROM ads_collection_runs WHERE provider IN ('YANDEX_DIRECT','TELEGRAM_ADS') AND ads_account_id IS NULL")).scalar_one()
    unresolved_health = bind.execute(sa.text("SELECT count(*) FROM ads_data_health WHERE (promotion_object_type = 'ACCOUNT' OR destination_platform = 'TELEGRAM_ADS') AND ads_account_id IS NULL")).scalar_one()
    if unresolved_reports or unresolved_runs or unresolved_health:
        raise RuntimeError(
            "ADS_ACCOUNT_OWNERSHIP_BACKFILL_UNRESOLVED "
            f"reports={unresolved_reports} collection_runs={unresolved_runs} data_health={unresolved_health}"
        )


def downgrade() -> None:
    op.drop_index("ix_ads_data_health_ads_account_id", table_name="ads_data_health")
    op.drop_constraint("fk_ads_data_health_ads_account", "ads_data_health", type_="foreignkey")
    op.drop_column("ads_data_health", "ads_account_id")
    op.drop_index("ix_ads_collection_runs_ads_account_id", table_name="ads_collection_runs")
    op.drop_constraint("fk_ads_collection_runs_ads_account", "ads_collection_runs", type_="foreignkey")
    op.drop_column("ads_collection_runs", "ads_account_id")
    op.drop_index("ix_ads_report_runs_ads_account_id", table_name="ads_report_runs")
    op.drop_constraint("fk_ads_report_runs_ads_account", "ads_report_runs", type_="foreignkey")
    op.drop_column("ads_report_runs", "ads_account_id")
    op.drop_index("ix_ads_accounts_execution_region", table_name="ads_accounts")
    op.drop_index("ix_ads_accounts_storage_region", table_name="ads_accounts")
    op.drop_column("ads_accounts", "execution_region")
    op.drop_column("ads_accounts", "storage_region")
