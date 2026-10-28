"""回答里的图：[IMG:n:j] 按本会话的引用目录找到图、给渠道账号签名后在进程内取回，随最后一帧交给平台；
按出现先后去重、最多 10 张，取不到的跳过；文字里不留放图标记。进程内取图只收图片。"""

import pytest
from fastapi import FastAPI, Response

from knowwork_server.channels.pictures import MAX_PICTURES, InProcessFetch, Pictures
from knowwork_server.knowledge.citations import CitationCatalog, CitationEntry
from knowwork_server.knowledge.models import Evidence, EvidenceImage

from .conftest import FakeConnection, answer, make_channel
from .test_service import message

pytestmark = pytest.mark.anyio


def catalog(*image_lists):
    entries = tuple(CitationEntry(number, Evidence(source_id="builtin", kb_id="kb1", doc_ref=f"d{number}",
                                                   span_ref=f"s{number}", title="报告", text="正文",
                                                   images=tuple(images)))
                    for number, images in enumerate(image_lists, start=1))
    return CitationCatalog(entries=entries)


class Catalogs:
    def __init__(self, value):
        self.value, self.asked = value, []

    async def load(self, workspace_id, catalog_id):
        self.asked.append((workspace_id, catalog_id))
        return self.value


def image(name, caption=""):
    return EvidenceImage(ref=name, caption=caption, url=f"/api/workspaces/ws/library/bases/kb1/images/{name}")


async def test_marks_become_pictures_in_order(env):
    fetched = []

    async def fetch(path):
        fetched.append(path)
        return None if "bad" in path else (path.encode(), "image/png")

    catalogs = Catalogs(catalog([image("a", "流程图"), image("bad")], [image("c")]))
    pictures = Pictures(catalogs, lambda url, user: f"{url}?uid={user}", fetch)
    channel = await make_channel(env)
    env.channels.service._pictures = pictures
    env.conversations.scripts = [answer(1, "流程见下图[IMG:1:0]，另见[IMG:2:0][IMG:1:0][IMG:1:1][IMG:9:0][IMG:1:5]。")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    [stream] = connection.streams
    assert stream.final == "流程见下图，另见。"
    # 按渠道账号签名；重复的只取一次；目录里没有的、取不到的跳过
    assert fetched == [f"/api/workspaces/ws/library/bases/kb1/images/{name}?uid={channel.id}"
                       for name in ("a", "c", "bad")]
    assert [(p.caption, p.mime) for p in stream.pictures] == [("流程图", "image/png"), ("", "image/png")]
    assert catalogs.asked == [(env.ws, env.conversations.created[0][0].id)]


async def test_no_more_than_ten_pictures():
    async def fetch(path):
        return b"x", "image/jpeg"

    many = Pictures(Catalogs(catalog([image(str(i)) for i in range(15)])), lambda url, user: url, fetch)
    text = "".join(f"[IMG:1:{i}]" for i in range(15))
    assert len(await many.of("ws", "c1", "u1", text)) == MAX_PICTURES
    assert await many.of("ws", "c1", "u1", "没有图") == []


async def test_in_process_fetch_only_takes_images():
    app = FastAPI()

    @app.get("/api/pic")
    async def pic():
        return Response(b"\x89PNG", media_type="image/png")

    @app.get("/api/page")
    async def page():
        return Response("<html>", media_type="text/html")

    fetch = InProcessFetch()
    assert await fetch("/api/pic") is None  # 还没接上 app
    fetch.bind(app)
    assert await fetch("/api/pic") == (b"\x89PNG", "image/png")
    assert await fetch("/api/page") is None
    assert await fetch("/api/missing") is None
