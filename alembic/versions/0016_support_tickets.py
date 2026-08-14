"""Add MAXIBOT support tickets and support input state."""
from alembic import op
import sqlalchemy as sa

revision = "0016_support_tickets"
down_revision = "0015_channel_slug_registry"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("admins", sa.Column("support_input_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("admins", sa.Column("support_input_ticket_id", sa.String(length=36), nullable=True))
    sa.Sequence("support_ticket_display_number_seq", start=1001).create(op.get_bind())
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("display_number", sa.Integer(), nullable=False, unique=True, server_default=sa.text("nextval('support_ticket_display_number_seq')")),
        sa.Column("max_user_id", sa.String(length=128), nullable=False),
        sa.Column("source_private_chat_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="NEW"),
        sa.Column("support_chat_id", sa.String(length=128), nullable=False),
        sa.Column("support_chat_message_id", sa.String(length=128), nullable=True),
        sa.Column("source_user_name", sa.String(length=255), nullable=False, server_default="Пользователь"),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_support_tickets_display_number", "support_tickets", ["display_number"], unique=True)
    op.create_index("ix_support_tickets_support_chat_message_id", "support_tickets", ["support_chat_message_id"], unique=True)
    op.create_index("ix_support_tickets_max_user_id", "support_tickets", ["max_user_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_table(
        "support_ticket_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ticket_id", sa.String(length=36), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
        sa.Column("support_chat_message_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("source_user_id", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="in"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_support_ticket_messages_ticket_id", "support_ticket_messages", ["ticket_id"])
    op.create_index("ix_support_ticket_messages_support_chat_message_id", "support_ticket_messages", ["support_chat_message_id"], unique=True)
    op.create_index("ix_support_ticket_messages_source_message_id", "support_ticket_messages", ["source_message_id"])

def downgrade():
    op.drop_table("support_ticket_messages")
    op.drop_table("support_tickets")
    sa.Sequence("support_ticket_display_number_seq").drop(op.get_bind())
    op.drop_column("admins", "support_input_ticket_id")
    op.drop_column("admins", "support_input_mode")
