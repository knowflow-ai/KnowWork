"""Alembic 运行环境。连接只由 migrate.py 通过 config.attributes 传入，不支持离线模式。"""

from alembic import context

from knowwork_server.db.registry import load_all_models

target_metadata = load_all_models()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("请通过 knowwork_server.db.migrate 运行迁移")
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("不支持离线迁移")
run_migrations_online()
