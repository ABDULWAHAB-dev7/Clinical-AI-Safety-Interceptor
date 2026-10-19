"""Alembic environment. The database comes from INTERCEPTOR_DATABASE_URL, in either form:

    postgresql://user:password@host:5432/dbname        (a URL)
    host=127.0.0.1 port=5432 dbname=interceptor user=...   (libpq key=value, as the service itself accepts)
"""

import os
from logging.config import fileConfig

from alembic import context
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL, make_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # the schema is plain SQL (db/schema.sql), not SQLAlchemy models


def database_url():
    raw = os.environ.get("INTERCEPTOR_DATABASE_URL")
    if not raw:
        raise RuntimeError("INTERCEPTOR_DATABASE_URL is required")
    if "://" in raw:
        url = make_url(raw)
        return url.set(drivername="postgresql+psycopg")
    parts = conninfo_to_dict(raw)
    return URL.create(
        "postgresql+psycopg",
        username=parts.get("user"),
        password=parts.get("password"),
        host=parts.get("host"),
        port=int(parts["port"]) if parts.get("port") else None,
        database=parts.get("dbname"),
    )


def run_migrations_offline():
    context.configure(url=database_url().render_as_string(hide_password=False), literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
