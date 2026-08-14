"""Use the user-facing default welcome copy."""
from alembic import op

revision = "0004_welcome_copy"
down_revision = "0003_opaque_channel_slugs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE channels SET welcome_text = '✅ Напишите ваш вопрос — я передам администратору.\\n\\nБот создан с помощью Предложка' WHERE welcome_text LIKE '%Бот создан с помощью MAXIBOT%'")


def downgrade():
    pass
