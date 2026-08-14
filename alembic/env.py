from alembic import context
from sqlalchemy import create_engine
from app.core.config import settings
from app.db.base import Base
from app.db import models

target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=settings.database_url.replace('+asyncpg', ''), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable = create_engine(settings.database_url.replace('+asyncpg', ''))
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
