"""Add owner WebAuthn credentials, ceremonies and sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0064_web_admin_passkeys"
down_revision = "0063_channel_subscription_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_admin_webauthn_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_id", sa.String(36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_id", sa.String(512), nullable=False),
        sa.Column("credential_public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index("ix_web_admin_webauthn_credentials_admin_id", "web_admin_webauthn_credentials", ["admin_id"])
    op.create_index("ix_web_admin_webauthn_credentials_credential_id", "web_admin_webauthn_credentials", ["credential_id"])
    op.create_table(
        "web_admin_auth_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_id", sa.String(36), sa.ForeignKey("admins.id", ondelete="CASCADE")),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("challenge", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_admin_auth_challenges_admin_id", "web_admin_auth_challenges", ["admin_id"])
    op.create_index("ix_web_admin_auth_challenges_purpose", "web_admin_auth_challenges", ["purpose"])
    op.create_index("ix_web_admin_auth_challenges_expires_at", "web_admin_auth_challenges", ["expires_at"])
    op.create_table(
        "web_admin_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_id", sa.String(36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_web_admin_sessions_admin_id", "web_admin_sessions", ["admin_id"])
    op.create_index("ix_web_admin_sessions_token_hash", "web_admin_sessions", ["token_hash"])
    op.create_index("ix_web_admin_sessions_expires_at", "web_admin_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("web_admin_sessions")
    op.drop_table("web_admin_auth_challenges")
    op.drop_table("web_admin_webauthn_credentials")
