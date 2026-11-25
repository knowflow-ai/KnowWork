"""上传的插件：压缩包检查、空间插件的档与给谁用、个人插件与管控开关、每个人生效的清单。"""

import io
import json
import tarfile
import uuid

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.plugins import build_my_uploads_router, build_plugins, build_uploads_router
from knowwork_server.plugins.archive import ArchiveInvalid, inspect_archive
from knowwork_server.skills.blobstore import LocalBlobStore

pytestmark = pytest.mark.anyio
GZIP = {"content-type": "application/gzip"}


def tgz(name="kw-demo-notes", version="1.0.0", *, code="export const name = 'x'\n", manifest=None, extra=None,
        root="package") -> bytes:
    """造一个插件包：package.json、配置补丁、一份代码；extra 是另外要放的（路径 → 内容或 TarInfo）。"""
    files = {
        "package.json": json.dumps(manifest if manifest is not None else {
            "name": name, "version": version, "description": "演示插件",
            "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}}).encode(),
        "cordis.patch.yml": b"- insert:\n    - id: demo\n      name: demo\n",
        "index.mjs": code.encode(),
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, content in files.items():
            if isinstance(content, tarfile.TarInfo):
                archive.addfile(content)
                continue
            info = tarfile.TarInfo(f"{root}/{path}" if root else path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_inspect_reads_identity_and_scans_own_code_only():
    code = ("import { readFile } from 'node:fs/promises'\nimport { spawn } from 'child_process'\n"
            "const key = process.env.SECRET\nctx.on('llm/stream', () => {})\n")
    vendored = b"fetch('https://example.com')\n"
    found = inspect_archive(tgz(code=code, extra={"node_modules/dep/index.js": vendored}))
    assert (found.name, found.version, found.description) == ("kw-demo-notes", "1.0.0", "演示插件")
    assert found.capabilities == ("fs", "process", "env", "llm-stream"), "带的依赖里的 fetch 不算插件自己的"
    assert len(found.digest) == 64 and found.size > 0
    assert inspect_archive(tgz(code="await fetch('https://example.com')\n")).capabilities == ("network",)
    assert inspect_archive(tgz(name="@acme/notes")).name == "@acme/notes"


@pytest.mark.parametrize(("data", "code"), ids=lambda value: value if isinstance(value, str) else "", argvalues=[
    (b"", "empty_archive"),
    (b"not a tarball at all", "invalid_archive"),
    (tgz(manifest={"name": "x", "version": "1.0.0"}), "not_a_bundle"),
    (tgz(manifest={"name": "Bad Name", "version": "1.0.0", "dsh": {"bundle": {"patch": "./p.yml"}}}), "invalid_name"),
    (tgz(manifest={"name": "x", "version": "latest", "dsh": {"bundle": {"patch": "./p.yml"}}}), "invalid_version"),
    (tgz(manifest={"name": "x", "version": "1.0.0", "dsh": {"bundle": {"patch": "./missing.yml"}}}), "missing_patch"),
    (tgz(manifest={"name": "x", "version": "1.0.0", "dsh": {"bundle": {"patch": "../../etc/passwd"}}}), "unsafe_path"),
    (tgz(extra={"../escape.js": b"x"}), "unsafe_path"),
    (tgz(extra={"other.js": b"x"}, root="") , "unexpected_layout"),
])
def test_inspect_rejects_what_cannot_be_installed(data, code):
    with pytest.raises(ArchiveInvalid) as caught:
        inspect_archive(data)
    assert caught.value.code == code


def test_official_packages_must_be_declared_as_peers():
    code = "import { defineTool } from '@deepseek-ai/dsh-tools'\nconst x = await import('@deepseek-ai/dsh-session/types')\n"
    with pytest.raises(ArchiveInvalid) as caught:
        inspect_archive(tgz(code=code))
    assert caught.value.code == "undeclared_peer" and "@deepseek-ai/dsh-tools" in str(caught.value)
    declared = {"name": "x", "version": "1.0.0", "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
                "peerDependencies": {"@deepseek-ai/dsh-tools": "*"}}
    bundled = {"node_modules/@deepseek-ai/dsh-session/package.json": b"{}"}
    assert inspect_archive(tgz(code=code, manifest=declared, extra=bundled)).name == "x", "声明了的、自己带了的都算"


def test_inspect_rejects_links():
    link = tarfile.TarInfo("package/evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(ArchiveInvalid) as caught:
        inspect_archive(tgz(extra={"evil": link}))
    assert caught.value.code == "link_not_allowed"


def test_inspect_reads_title_detail_and_components_like_the_official_plugin_page():
    """照 DSH 官方插件页：标题、说明取语言文件（先中文后英文），说明文档先看 meta.detail 再看 README，组件取补丁插入的行。"""
    zh = {"meta": {"title": "周报助手", "description": "把零散记录整理成周报", "detail": "docs/DETAIL.zh.md"}}
    en = {"meta": {"title": "Weekly", "description": "Weekly report", "detail": "docs/DETAIL.md"}}
    patch = (b"- insert:\n    - id: main\n      name: kw-demo-notes\n"
             b"    - id: group\n      name: cordis:group\n      group: true\n      config:\n"
             b"        - id: helper\n          name: kw-demo-notes/helper\n"
             b"        - id: official\n          name: '@deepseek-ai/dsh-tool-goal'\n")
    found = inspect_archive(tgz(extra={
        "locale/zh.json": json.dumps(zh).encode(), "locale/en.json": json.dumps(en).encode(),
        "docs/DETAIL.zh.md": "\ufeff---\ntitle: x\n---\n# 周报助手\n\n用法见下。\n".encode(),
        "README.md": b"# English readme\n", "cordis.patch.yml": patch,
        "node_modules/dep/locale/zh.json": b'{"meta": {"title": "dep"}}'}))
    assert (found.title, found.description) == ("周报助手", "把零散记录整理成周报")
    assert found.detail == "# 周报助手\n\n用法见下。", "去掉 BOM 和 YAML 头"
    assert [(c.id, c.module, c.title, c.description) for c in found.components] == [
        ("main", "kw-demo-notes", "周报助手", "把零散记录整理成周报"),
        ("helper", "kw-demo-notes/helper", "kw-demo-notes/helper", ""),
        ("official", "@deepseek-ai/dsh-tool-goal", "@deepseek-ai/dsh-tool-goal", ""),
    ], "分组展开；不是包本身的只写模块名"

    # 没有语言文件：说明退回 package.json，说明文档用 README，标题空着（界面上显示包名）
    plain = inspect_archive(tgz(extra={"README.md": b"# Notes\n"}))
    assert (plain.title, plain.description, plain.detail) == ("", "演示插件", "# Notes")
    assert [(c.module, c.title) for c in plain.components] == [("demo", "demo")]
    # 只有英文的：用英文
    assert inspect_archive(tgz(extra={"locale/en.json": json.dumps(en).encode()})).title == "Weekly"
    # 指了说明文档但不合规矩或文件不在：同官方，不退回 README
    for detail in ("../secret.md", "docs/none.md", "https://example.com/x.md", "docs/x.txt"):
        broken = json.dumps({"meta": {"detail": detail}}).encode()
        assert inspect_archive(tgz(extra={"locale/zh.json": broken, "README.md": b"# x"})).detail == "", detail
    # 补丁不是合法 YAML：组件清单空着，不拦上传
    assert inspect_archive(tgz(extra={"cordis.patch.yml": b"- insert: [\n"})).components == ()


@pytest.fixture
async def env(sessionmaker, tmp_path):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="插件", owner_user_id="boss")
    for user in ("alice", "bob"):
        await access.add_member("boss", ws, user, "editor")
    plugins = build_plugins(sessionmaker, access, LocalBlobStore(tmp_path))

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_my_uploads_router(plugins.uploads, principal))
    app.include_router(build_uploads_router(plugins.uploads, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        yield ws, http, plugins.uploads


def by(user: str, **extra) -> dict:
    return {"x-user": user, **extra}


async def test_workspace_plugin_modes_audience_and_targets(env):
    ws, http, uploads = env
    base = f"/api/workspaces/{ws}"
    made = await http.post(f"{base}/plugins/uploads", content=tgz(), headers=by("boss", **GZIP))
    assert made.status_code == 201, made.text
    plugin = made.json()
    assert (plugin["name"], plugin["scope"], plugin["mode"], plugin["targets"]) == \
        ("kw-demo-notes", "workspace", "off", ["cloud", "device"])
    pid = plugin["id"]
    assert await uploads.effective(ws, "alice", "cloud") == (), "传完默认关闭"
    assert (await http.get(f"{base}/me/plugins/uploads", headers=by("alice"))).json()["plugins"] == []

    # 默认启用、全空间：每个人都有，自己不能关
    assert (await http.put(f"{base}/plugins/uploads/{pid}", json={"mode": "on"}, headers=by("boss"))).status_code == 200
    assert [item.name for item in await uploads.effective(ws, "alice", "cloud")] == ["kw-demo-notes"]
    mine = (await http.get(f"{base}/me/plugins/uploads", headers=by("alice"))).json()["plugins"]
    assert (mine[0]["enabled"], mine[0]["locked"], mine[0]["scope"]) == (True, True, "workspace")
    locked = await http.put(f"{base}/me/plugins/uploads/{pid}", json={"enabled": False}, headers=by("alice"))
    assert locked.status_code == 422

    # 收窄到 alice：bob 没有了；管理员一直有
    narrowed = await http.put(f"{base}/plugins/uploads/{pid}", json={"subjects": ["user:alice"]}, headers=by("boss"))
    assert narrowed.status_code == 200 and narrowed.json()["narrowed"] is True, narrowed.text
    assert len(await uploads.effective(ws, "alice", "cloud")) == 1
    assert await uploads.effective(ws, "bob", "cloud") == ()
    assert len(await uploads.effective(ws, "boss", "cloud")) == 1
    listed = (await http.get(f"{base}/plugins/uploads", headers=by("boss"))).json()
    assert listed["plugins"][0]["subjects"] == ["user:alice"]
    assert (await http.get(f"{base}/me/plugins/uploads/{pid}/archive", headers=by("bob"))).status_code == 404

    # 可选：给了的人自己开；只适用于本机对话时，云端不生效
    await http.put(f"{base}/plugins/uploads/{pid}", json={"mode": "optional", "subjects": ["role:editor"],
                                                           "targets": ["device"]}, headers=by("boss"))
    assert await uploads.effective(ws, "bob", "device") == ()
    assert (await http.put(f"{base}/me/plugins/uploads/{pid}", json={"enabled": True}, headers=by("bob"))).status_code == 200
    assert len(await uploads.effective(ws, "bob", "device")) == 1
    assert await uploads.effective(ws, "bob", "cloud") == ()
    device = (await http.get(f"{base}/me/plugins/device", headers=by("bob"))).json()["plugins"]
    assert device[0]["name"] == "kw-demo-notes" and len(device[0]["digest"]) == 64
    got = await http.get(f"{base}/me/plugins/uploads/{pid}/archive", headers=by("bob"))
    assert got.status_code == 200 and got.headers["x-plugin-digest"] == device[0]["digest"]
    assert inspect_archive(got.content).name == "kw-demo-notes"

    # 同名再传是升级：版本换了，档和开关不动
    again = await http.post(f"{base}/plugins/uploads", content=tgz(version="1.1.0"), headers=by("boss", **GZIP))
    assert again.json()["id"] == pid and again.json()["version"] == "1.1.0" and again.json()["mode"] == "optional"
    assert (await uploads.effective(ws, "bob", "device"))[0].version == "1.1.0"

    assert (await http.delete(f"{base}/plugins/uploads/{pid}", headers=by("boss"))).status_code == 204
    assert await uploads.effective(ws, "bob", "device") == ()


async def test_plugin_page_title_components_and_detail(env):
    """插件页：列表带作者写的标题、组件清单；说明文档单独取，管理员看空间里的任何一个，成员只看给了他的和自己装的。"""
    ws, http, _ = env
    base = f"/api/workspaces/{ws}"
    meta = json.dumps({"meta": {"title": "笔记助手", "description": "整理笔记"}}).encode()
    archive = tgz(extra={"locale/zh.json": meta, "README.md": "# 笔记助手\n\n用法".encode()})
    made = (await http.post(f"{base}/plugins/uploads", content=archive, headers=by("boss", **GZIP))).json()
    pid = made["id"]
    assert (made["title"], made["description"]) == ("笔记助手", "整理笔记")
    assert made["components"] == [{"id": "demo", "module": "demo", "title": "demo", "description": ""}]
    assert "detail" not in made, "说明文档不进列表"
    assert (await http.get(f"{base}/plugins/uploads/{pid}/detail", headers=by("boss"))).json() == {"detail": "# 笔记助手\n\n用法"}
    assert (await http.get(f"{base}/plugins/uploads/{pid}/detail", headers=by("alice"))).status_code == 403
    assert (await http.get(f"{base}/me/plugins/uploads/{pid}/detail", headers=by("alice"))).status_code == 404, "还没给他"
    await http.put(f"{base}/plugins/uploads/{pid}", json={"mode": "optional"}, headers=by("boss"))
    assert (await http.get(f"{base}/me/plugins/uploads/{pid}/detail", headers=by("alice"))).json()["detail"] == "# 笔记助手\n\n用法"
    mine = (await http.get(f"{base}/me/plugins/uploads", headers=by("alice"))).json()["plugins"][0]
    assert (mine["title"], mine["components"][0]["module"]) == ("笔记助手", "demo")

    # 个人插件转成空间插件：标题、说明文档、组件跟着过去
    own = (await http.post(f"{base}/me/plugins/uploads", content=tgz("bob-tool", extra={"locale/zh.json": meta,
                           "README.md": b"# bob"}), headers=by("bob", **GZIP))).json()
    assert (await http.get(f"{base}/me/plugins/uploads/{own['id']}/detail", headers=by("bob"))).json()["detail"] == "# bob"
    assert (await http.get(f"{base}/me/plugins/uploads/{own['id']}/detail", headers=by("alice"))).status_code == 404
    await http.post(f"{base}/me/plugins/uploads/{own['id']}/submit", headers=by("bob"))
    adopted = (await http.post(f"{base}/plugins/uploads/{own['id']}/adopt", headers=by("boss"))).json()
    assert (adopted["title"], adopted["components"]) == ("笔记助手", own["components"])
    assert (await http.get(f"{base}/plugins/uploads/{adopted['id']}/detail", headers=by("boss"))).json()["detail"] == "# bob"


async def test_personal_plugins_and_admin_controls(env):
    ws, http, uploads = env
    base = f"/api/workspaces/{ws}"
    made = await http.post(f"{base}/me/plugins/uploads", content=tgz("my-weekly"), headers=by("alice", **GZIP))
    assert made.status_code == 201, made.text
    pid = made.json()["id"]
    assert made.json()["scope"] == "personal" and made.json()["owner"] == "alice"
    assert [item.name for item in await uploads.effective(ws, "alice", "cloud")] == ["my-weekly"], "自己装的默认开着"
    assert await uploads.effective(ws, "bob", "cloud") == (), "只有本人用"
    assert (await http.get(f"{base}/me/plugins/uploads", headers=by("bob"))).json()["plugins"] == []
    assert (await http.delete(f"{base}/me/plugins/uploads/{pid}", headers=by("bob"))).status_code == 404

    # 提交给管理员：管理员转成空间插件（同一份压缩包、默认关闭），之后可以授权给别人
    assert (await http.post(f"{base}/me/plugins/uploads/{pid}/submit", headers=by("bob"))).status_code == 404
    submitted = await http.post(f"{base}/me/plugins/uploads/{pid}/submit", headers=by("alice"))
    assert submitted.status_code == 200 and submitted.json()["submitted"] is True
    assert (await http.post(f"{base}/plugins/uploads/{pid}/adopt", headers=by("alice"))).status_code == 403
    adopted = await http.post(f"{base}/plugins/uploads/{pid}/adopt", headers=by("boss"))
    assert adopted.status_code == 201, adopted.text
    assert (adopted.json()["scope"], adopted.json()["mode"], adopted.json()["name"]) == ("workspace", "off", "my-weekly")
    assert (await http.post(f"{base}/plugins/uploads/{pid}/adopt", headers=by("boss"))).status_code == 422, "已经有了"
    await http.put(f"{base}/plugins/uploads/{adopted.json()['id']}", json={"mode": "on"}, headers=by("boss"))
    assert [item.name for item in await uploads.effective(ws, "bob", "cloud")] == ["my-weekly"], "转成空间插件后别人也能用"
    assert (await http.delete(f"{base}/plugins/uploads/{adopted.json()['id']}", headers=by("boss"))).status_code == 204
    assert [item.name for item in await uploads.effective(ws, "alice", "cloud")] == ["my-weekly"], "个人那份还在"

    # 管理员看得到成员装了什么；禁用清单按包名拦
    listed = (await http.get(f"{base}/plugins/uploads", headers=by("boss"))).json()
    assert [(p["name"], p["owner"]) for p in listed["member_plugins"]] == [("my-weekly", "alice")]
    settings = {"allow_personal": True, "allow_personal_device": True, "blocked": ["my-weekly"]}
    assert (await http.put(f"{base}/plugins/settings", json=settings, headers=by("boss"))).status_code == 200
    assert await uploads.effective(ws, "alice", "cloud") == ()
    assert (await http.get(f"{base}/me/plugins/uploads", headers=by("alice"))).json()["plugins"][0]["blocked"] is True
    blocked = await http.post(f"{base}/me/plugins/uploads", content=tgz("my-weekly"), headers=by("bob", **GZIP))
    assert blocked.status_code == 422 and "禁用" in blocked.json()["detail"]

    # 不许给本机对话装个人插件：云端还在，本机没有
    settings = {"allow_personal": True, "allow_personal_device": False, "blocked": []}
    await http.put(f"{base}/plugins/settings", json=settings, headers=by("boss"))
    assert len(await uploads.effective(ws, "alice", "cloud")) == 1
    assert await uploads.effective(ws, "alice", "device") == ()

    # 关掉个人安装：装过的不再生效，也不能再装
    settings = {"allow_personal": False, "allow_personal_device": True, "blocked": []}
    await http.put(f"{base}/plugins/settings", json=settings, headers=by("boss"))
    assert await uploads.effective(ws, "alice", "cloud") == ()
    closed = await http.post(f"{base}/me/plugins/uploads", content=tgz("another"), headers=by("alice", **GZIP))
    assert closed.status_code == 422
    assert (await http.get(f"{base}/me/plugins/uploads", headers=by("alice"))).json()["can_install"] is False

    # 空间里有同名的：个人不能再装一份；已经装了的让位给空间的
    await http.put(f"{base}/plugins/settings", json={"allow_personal": True, "allow_personal_device": True,
                                                    "blocked": []}, headers=by("boss"))
    shared = await http.post(f"{base}/plugins/uploads", content=tgz("my-weekly", "2.0.0"), headers=by("boss", **GZIP))
    assert shared.status_code == 201
    assert await uploads.effective(ws, "alice", "cloud") == (), "空间的同名插件还没开，个人那份也不算"
    same = await http.post(f"{base}/me/plugins/uploads", content=tgz("my-weekly"), headers=by("bob", **GZIP))
    assert same.status_code == 422 and "已经有" in same.json()["detail"]

    assert (await http.delete(f"{base}/me/plugins/uploads/{pid}", headers=by("alice"))).status_code == 204


async def test_permissions_and_bad_uploads(env):
    ws, http, _ = env
    base = f"/api/workspaces/{ws}"
    assert (await http.post(f"{base}/plugins/uploads", content=tgz(), headers=by("alice", **GZIP))).status_code == 403
    assert (await http.get(f"{base}/plugins/uploads", headers=by("alice"))).status_code == 403
    assert (await http.put(f"{base}/plugins/settings", json={"allow_personal": False, "allow_personal_device": False},
                           headers=by("alice"))).status_code == 403
    assert (await http.get(f"{base}/me/plugins/uploads", headers=by("mallory"))).status_code == 403
    assert (await http.get(f"{base}/me/plugins/device", headers=by("mallory"))).status_code == 403
    bad = await http.post(f"{base}/plugins/uploads", content=b"nope", headers=by("boss", **GZIP))
    assert bad.status_code == 422 and ".tgz" in bad.json()["detail"]
    assert (await http.post(f"{base}/plugins/uploads", content=tgz(),
                            headers=by("boss", **{"content-type": "text/plain"}))).status_code == 415
    wrong = await http.post(f"{base}/plugins/uploads?targets=moon", content=tgz(), headers=by("boss", **GZIP))
    assert wrong.status_code == 422


async def test_package_names_of_builtin_plugins_are_reserved(env):
    """预置插件的键和上传插件的包名共用登记表与个人开关：叫 goal 的包会覆盖预置「长任务目标」那一行、开关串在一起。"""
    ws, http, uploads = env
    base = f"/api/workspaces/{ws}"
    for path, user in ((f"{base}/plugins/uploads", "boss"), (f"{base}/me/plugins/uploads", "alice")):
        refused = await http.post(path, content=tgz(name="goal"), headers=by(user, **GZIP))
        assert refused.status_code == 422 and "预置插件重名" in refused.json()["detail"]
    shared, personal = await uploads.workspace_list("boss", ws)
    assert shared == () and personal == (), "什么都没存"
