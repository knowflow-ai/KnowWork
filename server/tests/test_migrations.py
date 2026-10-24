"""迁移合同：空库升级到最新后，表结构与模型完全一致，且所有带工作空间的业务表都开了行级安全。"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.db import migrate
from knowwork_server.db.base import Base
from knowwork_server.db.migrate import (
    MIGRATIONS_DIR,
    alembic_config,
    current_revision,
    migrate_for_settings,
    upgrade_to_head,
)
from knowwork_server.db.registry import MODEL_MODULES, RLS_TABLES, load_all_models

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def main_line_only(monkeypatch):
    """这里测开源主线的迁移合同。装着扩展的环境里也只升级主线：扩展的分支有它自己的合同测试。"""
    monkeypatch.setattr(migrate, "_installed_branches", lambda: migrate.MAIN_LINE)

# 带 workspace_id 但有意不做行级安全的表：API Key 跨空间归属于用户，按 user_id 判定。
RLS_EXEMPT = {"api_key"}
# pg_policies 里规范化后的策略条件（USING 与 WITH CHECK 都应如此）
ISOLATION_CONDITION = "((workspace_id)::text = current_setting('app.workspace_id'::text, true))"


def _head() -> str:
    return ScriptDirectory.from_config(alembic_config("postgresql://")).get_current_head()


def registered_metadata() -> MetaData:
    """只取 db/registry.py 登记过的模型包的表。

    测试收集时会导入各包的模型，还没登记（在功能分支上、迁移由主线统一写）的新表也会挂到 Base.metadata 上；
    迁移合同只对已登记的模型成立，登记一行之后这里就会要求补上迁移。
    """
    load_all_models()
    metadata = MetaData()
    for mapper in Base.registry.mappers:
        if mapper.class_.__module__ in MODEL_MODULES:
            mapper.local_table.to_metadata(metadata)
    return metadata


async def _diff(url):
    metadata = registered_metadata()
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync, opts={"compare_type": True}), metadata)
            )
    finally:
        await engine.dispose()


async def test_upgrade_matches_models(fresh_url):
    await upgrade_to_head(fresh_url)
    assert await _diff(fresh_url) == []
    assert await current_revision(fresh_url) is not None


async def test_upgrade_is_idempotent(fresh_url):
    await upgrade_to_head(fresh_url)
    first = await current_revision(fresh_url)
    await upgrade_to_head(fresh_url)
    assert await current_revision(fresh_url) == first


async def test_every_workspace_table_has_row_level_security(fresh_url):
    await upgrade_to_head(fresh_url)
    metadata = registered_metadata()
    with_workspace = {t.name for t in metadata.tables.values() if "workspace_id" in t.columns}
    assert with_workspace - RLS_EXEMPT == set(RLS_TABLES), "新的带 workspace_id 的表要登记进 RLS_TABLES 或豁免名单"
    engine = create_async_engine(fresh_url)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(
                "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity AND relkind = 'r'"
            ))).scalars().all()
            policies = (await connection.execute(text(
                "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public'"
            ))).all()
    finally:
        await engine.dispose()
    assert set(rows) == set(RLS_TABLES)
    assert sorted(row.tablename for row in policies) == sorted(RLS_TABLES), "每张行级安全表恰好一条策略"
    for row in policies:
        assert (row.policyname, row.cmd) == ("workspace_isolation", "ALL"), row
        assert row.qual == ISOLATION_CONDITION, row
        assert row.with_check == ISOLATION_CONDITION, row


async def test_concurrent_upgrades_on_empty_database_all_succeed(fresh_url):
    """多个实例同时对空库启动：advisory 锁让它们排队，后到的看到已是最新便什么都不做。"""
    results = await asyncio.gather(*(upgrade_to_head(fresh_url) for _ in range(5)), return_exceptions=True)
    assert [r for r in results if isinstance(r, BaseException)] == []
    assert await current_revision(fresh_url) == _head()
    assert await _diff(fresh_url) == []


async def test_lock_timeout_fails_fast_when_ddl_is_blocked(fresh_url):
    """迁移被业务连接挡住时按 lock_timeout 失败，而不是让启动无限期挂住。"""
    await upgrade_to_head(fresh_url)
    holder = create_async_engine(fresh_url)
    try:
        async with holder.begin() as connection:
            await connection.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await asyncio.wait_for(upgrade_to_head(fresh_url, lock_timeout_seconds=0.2), 10)
    finally:
        await holder.dispose()


async def test_lock_timeout_must_be_positive(fresh_url):
    with pytest.raises(ValueError):
        await upgrade_to_head(fresh_url, lock_timeout_seconds=0)


@pytest.mark.parametrize("role", ["", "App", "1app", 'x"; DROP TABLE y; --', "a" * 64, "app-role"])
async def test_app_role_name_is_validated_before_touching_the_database(fresh_url, role):
    with pytest.raises(ValueError):
        await upgrade_to_head(fresh_url, app_role=role)
    assert await current_revision(fresh_url) is None, "校验失败不得留下半截迁移"


async def test_app_role_must_exist(fresh_url):
    with pytest.raises(RuntimeError, match="不存在"):
        await upgrade_to_head(fresh_url, app_role="no_such_role_kw")
    assert await current_revision(fresh_url) is None, "授权失败时整个迁移回滚"


async def test_app_role_cannot_be_superuser_or_migrator(fresh_url):
    with pytest.raises(RuntimeError, match="超级用户"):
        await upgrade_to_head(fresh_url, app_role="postgres")


async def _execute(url, sql):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(sql))
            return result.scalar() if result.returns_rows else None
    finally:
        await engine.dispose()


async def test_app_role_gets_crud_but_does_not_own_tables(app_db):
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role)
    owned = await _execute(app_db.owner_url, f"SELECT count(*) FROM pg_tables WHERE tableowner = '{app_db.role}'")
    assert owned == 0
    with pytest.raises(DBAPIError, match="must be owner"):
        await _execute(app_db.app_url, "ALTER TABLE conversation DISABLE ROW LEVEL SECURITY")
    with pytest.raises(DBAPIError, match="permission denied"):
        await _execute(app_db.app_url, "UPDATE alembic_version SET version_num = 'x'")
    assert await _execute(app_db.app_url, "SELECT version_num FROM alembic_version") == _head()
    # 自增序列也授权了：casbin_rule.id 普通账号能插入
    await _execute(app_db.app_url, "INSERT INTO workspace (id, name) VALUES ('w1', 'W')")
    engine = create_async_engine(app_db.app_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.workspace_id', 'w1', true)"))
            await connection.execute(text(
                "INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3) VALUES ('w1', 'g', 'a', 'b', 'w1', '')"))
    finally:
        await engine.dispose()


async def test_tables_created_by_later_migrations_are_granted_too(app_db):
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role)
    await _execute(app_db.owner_url, "CREATE TABLE later_table (id serial PRIMARY KEY, v text)")
    await _execute(app_db.app_url, "INSERT INTO later_table (v) VALUES ('ok')")
    assert await _execute(app_db.app_url, "SELECT v FROM later_table") == "ok"


async def test_migrate_for_settings_uses_migration_url_and_grants(app_db):
    settings = SimpleNamespace(database_url=app_db.app_url, migration_database_url=app_db.owner_url,
                               app_db_role=app_db.role, migration_lock_timeout_seconds=5.0)
    await migrate_for_settings(settings)
    assert await current_revision(app_db.app_url) == _head()


async def test_migrate_for_settings_falls_back_to_database_url(fresh_url):
    settings = SimpleNamespace(database_url=fresh_url, migration_database_url="", app_db_role="",
                               migration_lock_timeout_seconds=5.0)
    await migrate_for_settings(settings)
    assert await current_revision(fresh_url) == _head()


@pytest.fixture
async def app_role_url(app_db):
    """正式路径：owner 账号迁移并授权，业务用普通账号连接。"""
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role)
    return app_db.app_url


async def test_workspace_sessionmaker_passes_row_level_security(app_role_url):
    from knowwork_server.conversations.models import Conversation
    from knowwork_server.db.base import make_engine, make_sessionmaker, make_workspace_sessionmaker

    engine = make_engine(app_role_url)
    try:
        scoped = make_workspace_sessionmaker(engine, "default")
        async with scoped() as session:
            session.add(Conversation(id="c_rls", workspace_id="default", user_id="u1", title=""))
            await session.commit()
        async with scoped() as session:
            assert await session.get(Conversation, "c_rls") is not None

        other = make_workspace_sessionmaker(engine, "other")
        async with other() as session:
            assert await session.get(Conversation, "c_rls") is None, "别的空间读不到"
            session.add(Conversation(id="c_cross", workspace_id="default", user_id="u1", title=""))
            with pytest.raises(Exception, match="row-level security"):
                await session.commit()

        async with make_sessionmaker(engine)() as session:
            assert await session.get(Conversation, "c_rls") is None, "不声明空间时一行都读不到"
    finally:
        await engine.dispose()


async def test_access_service_works_under_app_role(app_role_url):
    """权限包在真实权限下：建默认空间、加成员（写 casbin_rule 自增列）、重新加载规则。"""
    from knowwork_server.access.service import AccessService
    from knowwork_server.db.base import make_engine, make_sessionmaker

    engine = make_engine(app_role_url)
    try:
        access = AccessService(make_sessionmaker(engine))
        assert await access.reload()
        workspace_id = await access.ensure_default_workspace("alice")
        assert await access.ensure_default_workspace("bob") == workspace_id, "已存在则不动"
        await access.add_member("alice", workspace_id, "carol", "editor")
        members = await access.list_members("alice", workspace_id)
        assert {(m.user_id, m.role) for m in members} == {("alice", "owner"), ("carol", "editor")}

        reloaded = AccessService(make_sessionmaker(engine))
        assert await reloaded.reload()
        assert {m.user_id for m in await reloaded.list_members("carol", workspace_id)} == {"alice", "carol"}
    finally:
        await engine.dispose()


async def _alembic(url, action, revision):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            def run(sync):
                config = alembic_config(url)
                config.attributes["connection"] = sync
                action(config, revision)

            await connection.run_sync(run)
    finally:
        await engine.dispose()


async def _rules(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT workspace_id, v0, v2, v3 FROM casbin_rule WHERE ptype = 'p'"))
            return {tuple(row) for row in rows}
    finally:
        await engine.dispose()


async def test_0014_rewrites_knowledge_source_grants_as_knowledge_base_grants(fresh_url):
    """知识源上的显式授权改写成整个来源的知识库授权（一律 use），已删知识源的孤儿授权直接删；
    默认规则与别的授权不动；回退还原整个来源的那种。"""
    await _alembic(fresh_url, command.upgrade, "0013")
    before = {("w1", "role:admin", "knowledge_source:*", "owner"), ("w1", "user:u1", "knowledge_source:ks_a", "use"),
              ("w1", "group:g", "knowledge_source:ks_b", "edit"), ("w1", "role:viewer", "knowledge_source:*", "use"),
              ("w1", "user:u1", "skill:s", "use"), ("w2", "user:u2", "knowledge_source:ks_c", "owner"),
              ("w1", "user:u3", "knowledge_source:ks_gone", "use")}
    engine = create_async_engine(fresh_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W1'), ('w2', 'W2')"))
            for workspace_id, source_id in (("w1", "ks_a"), ("w1", "ks_b"), ("w2", "ks_c")):
                await connection.execute(text(
                    "INSERT INTO knowledge_source (id, workspace_id, name, kind, base_url, config, secret_last4,"
                    " enabled, created_by) VALUES (:s, :w, :s, 'knowflow', 'http://kf', '{}', '', true, 'u0')"),
                    {"s": source_id, "w": workspace_id})
            for workspace_id, subject, obj, act in before:
                await connection.execute(text(
                    "INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3) VALUES (:w, 'p', :s, :w, :o, :a)"),
                    {"w": workspace_id, "s": subject, "o": obj, "a": act})
    finally:
        await engine.dispose()
    await upgrade_to_head(fresh_url)
    assert await _rules(fresh_url) == {
        ("w1", "role:admin", "knowledge_source:*", "owner"), ("w1", "user:u1", "knowledge_base:ks_a.*", "use"),
        ("w1", "group:g", "knowledge_base:ks_b.*", "use"), ("w1", "role:viewer", "knowledge_base:*", "use"),
        ("w1", "user:u1", "skill:s", "use"), ("w2", "user:u2", "knowledge_base:ks_c.*", "use")}
    await _alembic(fresh_url, command.downgrade, "0013")
    restored = await _rules(fresh_url)
    assert ("w1", "user:u1", "knowledge_source:ks_a", "use") in restored
    assert not any(obj.endswith(".*") for _, _, obj, _ in restored)


async def test_0015_moves_personal_key_sources_to_shared_and_drops_personal_keys(fresh_url):
    """个人 Key 方式的知识源改成统一账号（密钥留空，管理员补填前检索会明确报错），个人 Key 表删掉；回退重建空表。"""
    await _alembic(fresh_url, command.upgrade, "0014")
    engine = create_async_engine(fresh_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W1')"))
            for source_id, mode in (("ks_p", "api_key"), ("ks_a", "assertion")):
                await connection.execute(text(
                    "INSERT INTO knowledge_source (id, workspace_id, name, kind, base_url, config, secret_ciphertext,"
                    " secret_last4, enabled, created_by) VALUES (:s, 'w1', :s, 'knowflow', 'http://kf',"
                    " jsonb_build_object('auth_mode', CAST(:m AS text), 'dataset_ids', jsonb_build_array()),"
                    " 'sealed', 'abcd', true, 'u0')"), {"s": source_id, "m": mode})
            await connection.execute(text(
                "INSERT INTO knowledge_user_credential (source_id, user_id, workspace_id, ciphertext, last4)"
                " VALUES ('ks_p', 'u1', 'w1', 'sealed', 'wxyz')"))
    finally:
        await engine.dispose()
    await upgrade_to_head(fresh_url)
    rows = await _execute(fresh_url, "SELECT json_agg(json_build_array(id, config->>'auth_mode', secret_ciphertext)"
                                     " ORDER BY id) FROM knowledge_source")
    assert rows == [["ks_a", "assertion", "sealed"], ["ks_p", "shared", None]]
    assert await _execute(fresh_url, "SELECT to_regclass('knowledge_user_credential')") is None
    await _alembic(fresh_url, command.downgrade, "0014")
    assert await _execute(fresh_url, "SELECT count(*) FROM knowledge_user_credential") == 0


def test_historical_migrations_do_not_import_application_code():
    """历史迁移只依赖 Alembic 与 SQLAlchemy：引用应用代码会让以后改代码时悄悄改变重放结果。"""
    versions = MIGRATIONS_DIR / "versions"
    offenders = [path.name for path in versions.glob("*.py") if "knowwork_server" in path.read_text("utf-8")]
    assert offenders == []


def test_settings_validate_app_db_role():
    from pydantic import ValidationError

    from knowwork_server.config import Settings

    base = {"master_key": "k" * 32, "host_token": "t" * 32}
    assert Settings(**base, app_db_role="knowwork_app").app_db_role == "knowwork_app"
    assert Settings(**base).app_db_role == ""
    for bad in ("Knowwork", "kw-app", 'x"y'):
        with pytest.raises(ValidationError):
            Settings(**base, app_db_role=bad)
    with pytest.raises(ValidationError):
        Settings(**base, migration_lock_timeout_seconds=0)


async def test_0017_moves_pending_attachment_parses_into_the_job_table(fresh_url):
    """排队中、运行中的附件解析任务搬成通用任务重新排队（worker 以前从没消费过它们），
    已失败的不搬；旧表删掉；回退时把还在排队的搬回旧表。"""
    await _alembic(fresh_url, command.upgrade, "0016")
    engine = create_async_engine(fresh_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W1')"))
            await connection.execute(text(
                "INSERT INTO conversation (id, workspace_id, user_id, title) VALUES ('c1', 'w1', 'u1', '')"))
            for attachment_id, status in (("a_q", "queued"), ("a_r", "running"), ("a_f", "failed")):
                await connection.execute(text(
                    "INSERT INTO attachment (id, workspace_id, conversation_id, user_id, filename, input_name,"
                    " parsed_name, kind, mime, size, digest, status, parse_status) VALUES (:a, 'w1', 'c1', 'u1',"
                    " :a, :a, :md, 'pdf', 'application/pdf', 1, 'd', 'ready', 'parsing')"),
                    {"a": attachment_id, "md": f"{attachment_id}.md"})
                await connection.execute(text(
                    "INSERT INTO attachment_parse_job (id, workspace_id, attachment_id, status, attempts, max_attempts)"
                    " VALUES (:j, 'w1', :a, :s, 1, 3)"), {"j": f"apj_{attachment_id}", "a": attachment_id, "s": status})
    finally:
        await engine.dispose()
    await upgrade_to_head(fresh_url)
    rows = await _execute(fresh_url, "SELECT json_agg(json_build_array(subject_id, kind, status, tries, max_tries, pool)"
                                     " ORDER BY subject_id) FROM job")
    assert rows == [["a_q", "attachment.parse", "queued", 0, 3, "core"],
                    ["a_r", "attachment.parse", "queued", 0, 3, "core"]]
    assert await _execute(fresh_url, "SELECT to_regclass('attachment_parse_job')") is None
    await _alembic(fresh_url, command.downgrade, "0016")
    assert await _execute(fresh_url, "SELECT json_agg(attachment_id ORDER BY attachment_id)"
                                     " FROM attachment_parse_job") == ["a_q", "a_r"]
    assert await _execute(fresh_url, "SELECT to_regclass('job')") is None


async def test_0018_types_old_locators_and_drops_unitless_boxes(fresh_url):
    """已存引用的定位改成带类型的 pdf 定位：留页码与章节路径，没有单位的旧框丢掉；回退还原成旧形状。"""
    await _alembic(fresh_url, command.upgrade, "0017")
    engine = create_async_engine(fresh_url)
    old = ('{"page": 3, "boxes": [{"page": 3, "x0": 10, "x1": 200, "top": 5, "bottom": 9}],'
           ' "section_path": ["第三章"]}')
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W1')"))
            await connection.execute(text(
                "INSERT INTO citation_catalog (id, workspace_id, size, \"limit\", truncated, dropped)"
                " VALUES ('c1', 'w1', 2, 50, false, 0)"))
            for number, locator in ((1, old), (2, None)):
                await connection.execute(text(
                    "INSERT INTO citation (catalog_id, workspace_id, number, source_id, doc_ref, span_ref, title, text,"
                    " locator) VALUES ('c1', 'w1', :n, 's', 'd', :span, 't', 'x', CAST(:loc AS jsonb))"),
                    {"n": number, "span": f"p{number}", "loc": locator})
    finally:
        await engine.dispose()
    await upgrade_to_head(fresh_url)
    rows = await _execute(fresh_url, "SELECT json_agg(locator ORDER BY number) FROM citation")
    assert rows == [{"type": "pdf", "page": 3, "section_path": ["第三章"]}, None]
    await _alembic(fresh_url, command.downgrade, "0017")
    assert await _execute(fresh_url, "SELECT locator FROM citation WHERE number = 1") == {
        "page": 3, "section_path": ["第三章"]}



@pytest.fixture
async def owner_db(fresh_url):
    """库和 public 模式归一个非超级用户（同部署里的 knowwork_owner）：FORCE 行级安全对表的所有者也生效，
    迁移改数据要逐个空间声明 app.workspace_id；超级用户会绕过行级安全，漏了声明测试也看不出来。"""
    role = f"own_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(fresh_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await connection.execute(text(f"ALTER SCHEMA public OWNER TO {role}"))
    yield SimpleNamespace(url=fresh_url.replace("postgres@", f"{role}@", 1), role=role)
    async with admin.connect() as connection:
        await connection.execute(text("ALTER SCHEMA public OWNER TO postgres"))
        await connection.execute(text(f"DROP OWNED BY {role}"))
        await connection.execute(text(f"DROP ROLE {role}"))
    await admin.dispose()


# v0.0.1 发布时 0002 建的模型分组两张表（发布后 0002 去掉了它们，见 0019）
_V001_MODEL_GROUPS = (
    ("CREATE TABLE model_group (id varchar(64) PRIMARY KEY, workspace_id varchar(64) NOT NULL,"
     " name varchar(100) NOT NULL, description varchar(500) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),"
     " CONSTRAINT uq_model_group_name UNIQUE (workspace_id, name))"),
    "CREATE INDEX ix_model_group_workspace_id ON model_group (workspace_id)",
    ("CREATE TABLE model_group_item (group_id varchar(64) REFERENCES model_group (id) ON DELETE CASCADE,"
     " model_id varchar(64) REFERENCES model (id) ON DELETE CASCADE, workspace_id varchar(64) NOT NULL,"
     " PRIMARY KEY (group_id, model_id))"),
    "CREATE INDEX ix_model_group_item_workspace_id ON model_group_item (workspace_id)",
    *(statement for table in ("model_group", "model_group_item") for statement in (
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY', f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        (f"CREATE POLICY workspace_isolation ON \"{table}\""
         " USING (workspace_id = current_setting('app.workspace_id', true))"
         " WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"))),
)


async def test_0019_brings_a_v001_database_up_to_the_current_models(owner_db, fresh_url):
    """v0.0.1 的库（0002 是改之前的）：补上文档解析表与思考开关列，删掉模型分组的表和授权，
    运行配置快照去掉 models（不去掉老会话一读快照就校验失败）；别的授权不动。新装的库这里什么都不做（见上面几条）。"""
    from knowwork_server.profile.snapshot import RuntimeProfile

    await _alembic(owner_db.url, command.upgrade, "0018")
    legacy = RuntimeProfile(workspace_id="w1", user_id="u1", role="editor", member=True).payload() | {
        "models": [{"id": "m1", "alias": "default", "name": "deepseek-chat"}]}
    kept = {("w1", "role:admin", "skill:*", "owner"), ("w1", "user:u1", "skill:s", "use"),
            ("w2", "role:admin", "knowledge_base:*", "owner")}
    granted = {("w1", "role:admin", "model_group:*", "owner"), ("w1", "role:viewer", "model_group:*", "use"),
               ("w1", "user:u1", "model_group:mg1", "use"), ("w2", "role:editor", "model_group:*", "use")}
    engine = create_async_engine(fresh_url)  # 超级用户，造数据不受行级安全限制
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE document_parser"))
            await connection.execute(text("ALTER TABLE model DROP COLUMN thinking"))
            await connection.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W1'), ('w2', 'W2')"))
            await connection.execute(text(f"SET ROLE {owner_db.role}"))  # 分组表也归库所有者，迁移才删得掉
            for statement in _V001_MODEL_GROUPS:
                await connection.execute(text(statement))
            await connection.execute(text("RESET ROLE"))
            await connection.execute(text(
                "INSERT INTO model_group (id, workspace_id, name, description) VALUES ('mg1', 'w1', '通用', '')"))
            for workspace_id, subject, obj, act in kept | granted:
                await connection.execute(text(
                    "INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3) VALUES (:w, 'p', :s, :w, :o, :a)"),
                    {"w": workspace_id, "s": subject, "o": obj, "a": act})
            await connection.execute(text(
                "INSERT INTO runtime_profile (workspace_id, version, user_id, snapshot)"
                " VALUES ('w1', 'v-old', 'u1', CAST(:s AS jsonb))"), {"s": json.dumps(legacy)})
    finally:
        await engine.dispose()

    await upgrade_to_head(owner_db.url)
    assert await _diff(fresh_url) == [], "升级上来的库与新装的库表结构一致"
    assert await _execute(fresh_url, "SELECT to_regclass('model_group')") is None
    assert await _execute(fresh_url, "SELECT to_regclass('model_group_item')") is None
    assert await _execute(fresh_url, "SELECT count(*) FROM pg_policies WHERE tablename = 'document_parser'") == 1
    assert await _execute(fresh_url, "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'document_parser'")
    assert await _rules(fresh_url) == kept
    snapshot = await _execute(fresh_url, "SELECT snapshot FROM runtime_profile WHERE version = 'v-old'")
    assert "models" not in snapshot
    RuntimeProfile.model_validate(snapshot)
