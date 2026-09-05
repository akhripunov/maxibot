"""Persist sanitized QA runs and suite results for the owner dashboard."""

from alembic import op
import sqlalchemy as sa


revision = "0065_qa_observability"
down_revision = "0064_web_admin_passkeys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("tested_sha", sa.String(64), nullable=False),
        sa.Column("current_main_sha", sa.String(64)),
        sa.Column("production_sha", sa.String(64)),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("total_tests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("production_status", sa.String(24), nullable=False, server_default="NOT_DEPLOYED"),
        sa.Column("production_smoke", sa.String(24), nullable=False, server_default="NOT_RUN"),
        sa.Column("artifact_sha256", sa.String(64)),
        sa.Column("source_path", sa.String(255)),
        sa.Column("failures_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("unverified_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("release_id"),
    )
    for name, columns in {
        "ix_qa_runs_release_id": ["release_id"],
        "ix_qa_runs_tested_sha": ["tested_sha"],
        "ix_qa_runs_current_main_sha": ["current_main_sha"],
        "ix_qa_runs_production_sha": ["production_sha"],
        "ix_qa_runs_verdict": ["verdict"],
        "ix_qa_runs_finished_at": ["finished_at"],
        "ix_qa_runs_created_at": ["created_at"],
    }.items():
        op.create_index(name, "qa_runs", columns)
    op.create_table(
        "qa_suite_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("qa_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suite_name", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_tests", sa.Integer()),
        sa.Column("passed", sa.Integer()),
        sa.Column("failed", sa.Integer()),
        sa.Column("skipped", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("failures_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("unverified_json", sa.Text(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("run_id", "suite_name", name="uq_qa_suite_result_run_name"),
    )
    op.create_index("ix_qa_suite_results_run_id", "qa_suite_results", ["run_id"])


def downgrade() -> None:
    op.drop_table("qa_suite_results")
    for name in (
        "ix_qa_runs_created_at",
        "ix_qa_runs_finished_at",
        "ix_qa_runs_verdict",
        "ix_qa_runs_production_sha",
        "ix_qa_runs_current_main_sha",
        "ix_qa_runs_tested_sha",
        "ix_qa_runs_release_id",
    ):
        op.drop_index(name, table_name="qa_runs")
    op.drop_table("qa_runs")
