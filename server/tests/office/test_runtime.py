"""Office 容器在业务服务里只建一个（build_office）：格式转换与扩展的在线编辑共用这一个容器池，闲置回收、地址缓存不会两边打架。
没配置时不建（不起容器、不连 Docker）；设了镜像按人起容器；本机开发连一个固定的、从源码起的 GenOffice。"""

from types import SimpleNamespace

import pytest

from knowwork_server.office import build_office
from knowwork_server.office.config import OfficeSettings
from knowwork_server.office.containers import OfficeContainers
from knowwork_server.office.files import LocalOfficeFiles

pytestmark = pytest.mark.anyio


def settings(**values) -> OfficeSettings:
    return OfficeSettings(_env_file=None, **values)


async def test_nothing_is_built_without_configuration():
    assert build_office(settings()) is None
    # 本机开发要 GenOffice 的 MCP 地址和文件目录两样，缺一样也不建
    assert build_office(settings(upstream_mcp_url="http://127.0.0.1:3193/mcp")) is None


async def test_an_image_gives_one_container_per_person(tmp_path):
    engine = SimpleNamespace(aclose=None)
    office = build_office(settings(image="knowwork-office:test", max_containers=3), engine=engine)
    try:
        assert isinstance(office.containers, OfficeContainers)
        assert office.files is office.containers
        assert office.containers._max == 3
    finally:
        await office.aclose()


async def test_local_development_uses_the_genoffice_it_is_given(tmp_path):
    office = build_office(settings(upstream_mcp_url="http://127.0.0.1:3193/mcp", files_root=tmp_path))
    try:
        assert office.containers is None
        assert isinstance(office.files, LocalOfficeFiles)
    finally:
        await office.aclose()


async def test_idle_editors_stop_first_by_default_and_zero_days_removes_right_away():
    """默认空闲 30 分钟先停、停 7 天才删，提前启动开着；保留天数设 0 回到空闲直接删。"""
    defaults = settings()
    assert (defaults.idle_minutes, defaults.keep_stopped_days, defaults.prewarm) == (30, 7, True)
    engine = SimpleNamespace(aclose=None)
    office = build_office(settings(image="knowwork-office:test"), engine=engine)
    removing = build_office(settings(image="knowwork-office:test", keep_stopped_days=0), engine=engine)
    try:
        assert office.containers.sweeper._keep_stopped == 7 * 24 * 3600
        assert removing.containers.sweeper._keep_stopped is None
    finally:
        await office.aclose()
        await removing.aclose()


async def test_deleting_a_conversation_clears_its_files_for_its_owner(tmp_path):
    office = build_office(settings(upstream_mcp_url="http://127.0.0.1:3193/mcp", files_root=tmp_path))
    (tmp_path / "conv-1" / "output").mkdir(parents=True)
    (tmp_path / "conv-1" / "output" / "a.docx").write_bytes(b"x")
    (tmp_path / ".knowwork" / "conv-1").mkdir(parents=True)
    (tmp_path / "conv-2").mkdir()
    try:
        await office.eraser()(SimpleNamespace(id="conv-1", workspace_id="ws1", user_id="alice"))
        assert not (tmp_path / "conv-1").exists() and not (tmp_path / ".knowwork" / "conv-1").exists()
        assert (tmp_path / "conv-2").exists()
    finally:
        await office.aclose()


def test_the_deployed_name_of_the_container_cap_still_works(monkeypatch):
    monkeypatch.setenv("KNOWWORK_OFFICE_MAX_EDITORS", "7")
    assert OfficeSettings(_env_file=None).max_containers == 7
    monkeypatch.setenv("KNOWWORK_OFFICE_MAX_CONTAINERS", "9")
    assert OfficeSettings(_env_file=None).max_containers == 9


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_cap_below_one_is_refused(monkeypatch, value):
    monkeypatch.setenv("KNOWWORK_OFFICE_MAX_CONTAINERS", value)
    with pytest.raises(ValueError):
        OfficeSettings(_env_file=None)


async def test_author_names_come_from_the_user_directory():
    """建容器时写进去的修订作者名：按用户 id 查名字，查不到是空（GenOffice 用它自己的默认名）。"""
    from knowwork_server.office import author_names

    async def get_many(ids):
        return {"alice": SimpleNamespace(name="张三")} if "alice" in ids else {}

    author_of = author_names(get_many)
    assert await author_of("alice") == "张三" and await author_of("bob") == ""
