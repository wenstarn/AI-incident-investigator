from logging.config import fileConfig

from alembic import context
from order_service.config import get_database_schema, get_database_url
from order_service.models import Base
from sqlalchemy import engine_from_config, pool
from sqlalchemy.schema import CreateSchema

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_schema = get_database_schema()
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))
config.attributes["database_schema"] = database_schema


def include_name(
    name: str | None, type_: str, parent_names: dict[str, str | None]
) -> bool:
    if type_ == "schema":
        return name == database_schema
    if type_ == "table":
        return parent_names.get("schema_name") == database_schema
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        version_table_schema=database_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 3},
    )
    with connectable.connect() as connection:
        connection.execute(CreateSchema(database_schema, if_not_exists=True))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            version_table_schema=database_schema,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
