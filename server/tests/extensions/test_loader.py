"""扩展加载：按授权启用；没有授权、授权里没有这个模块、校验出错、扩展本身坏了，都只跳过并写明原因。"""

from importlib.metadata import EntryPoint

import pytest

from knowwork_server.extensions import ENTRY_GROUP, VERIFIER_GROUP, discover

DEMO = EntryPoint("demo", "tests.extensions.demo:EXTENSION", ENTRY_GROUP)


def found(*entries: EntryPoint):
    def lookup(group: str):
        return [entry for entry in entries if entry.group == group]

    return lookup


def verifier(value: str) -> EntryPoint:
    return EntryPoint("license", f"tests.extensions.demo:{value}", VERIFIER_GROUP)


def test_without_any_extension_only_open_source_runs():
    extensions = discover(found())
    assert (extensions.license.status, extensions.installed, extensions.active) == ("none", (), ())


def test_licensed_extension_is_enabled():
    extensions = discover(found(DEMO, verifier("granting_demo")))
    assert [e.name for e in extensions.active] == ["demo"]
    assert extensions.license.customer == "测试客户" and extensions.skipped == ()
    assert extensions.rls_tables == ("demo_note",) and len(extensions.migration_dirs) == 1


@pytest.mark.parametrize(("license_entry", "status", "reason"), [
    (None, "none", "没有安装授权，只启用开源功能"),
    ("granting_other", "valid", "授权里没有「demo」模块"),
    ("Expired", "expired", "授权已于 2026-01-01 过期"),
    ("Exploding", "invalid", "授权校验出错，已按没有授权处理"),
    ("NOT_AN_EXTENSION", "invalid", "授权校验出错，已按没有授权处理"),
])
def test_unlicensed_extension_is_installed_but_skipped_with_reason(license_entry, status, reason):
    entries = (DEMO, verifier(license_entry)) if license_entry else (DEMO,)
    extensions = discover(found(*entries))
    assert extensions.license.status == status
    assert [e.name for e in extensions.installed] == ["demo"], "装了包就算已安装，迁移照跑"
    assert extensions.active == ()
    assert [(s.name, s.reason) for s in extensions.skipped] == [("demo", reason)]


def test_more_than_one_verifier_is_refused():
    extensions = discover(found(DEMO, verifier("granting_demo"), verifier("granting_other")))
    assert extensions.license.status == "invalid" and extensions.active == ()


def test_broken_entries_are_skipped():
    entries = (EntryPoint("gone", "tests.extensions.missing_module:X", ENTRY_GROUP),
               EntryPoint("odd", "tests.extensions.demo:NOT_AN_EXTENSION", ENTRY_GROUP), DEMO,
               verifier("granting_demo"))
    extensions = discover(found(*entries))
    assert [e.name for e in extensions.active] == ["demo"]
    assert [(s.name, s.reason) for s in extensions.skipped] == [
        ("gone", "扩展加载失败，详情见业务服务日志"), ("odd", "扩展加载失败，详情见业务服务日志")]
