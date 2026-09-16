from alembic import context
from sqlalchemy import create_engine, pool
from backend.db import Base

config = context.config
if context.is_offline_mode():
    context.configure(url=config.get_main_option('sqlalchemy.url'),target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable=create_engine(config.get_main_option('sqlalchemy.url'),poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
