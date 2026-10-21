"""连接真实的 MCP 服务（官方 SDK 起的 Streamable HTTP 服务）：列工具、调用、报错与连不上时的原因。"""

import pytest

from knowwork_server.tools.mcp.client import Endpoint, McpClient, McpUnavailable

from ..mcp_stub import McpStub
from ..support import free_port
from .conftest import AUTH

pytestmark = pytest.mark.anyio


async def test_list_and_call_tools(stub):
    client = McpClient()
    endpoint = Endpoint(url=stub.url, headers=AUTH, timeout_seconds=10)
    tools = {tool.name: tool for tool in await client.list_tools(endpoint)}
    assert set(tools) == {"add", "create_ticket", "delete_item", "kb.search"}
    assert tools["add"].annotations == {"readOnlyHint": True}
    assert tools["delete_item"].annotations["destructiveHint"] is True
    assert tools["create_ticket"].input_schema["required"] == ["title"]

    added = await client.call_tool(endpoint, "add", {"a": 2, "b": 3})
    assert (added.text, added.is_error) == ("5", False)
    failed = await client.call_tool(endpoint, "delete_item", {"item_id": "x1"})
    assert failed.is_error and "记录 x1 不存在" in failed.text
    assert stub.calls == [("add", {"a": 2, "b": 3})]


@pytest.mark.parametrize(("headers", "path", "reason"), [
    ({"Authorization": "Bearer wrong"}, "/mcp", "拒绝了访问（HTTP 401）"),
    ({}, "/mcp", "拒绝了访问（HTTP 401）"),
])
async def test_rejected_credentials_are_explained(stub, headers, path, reason):
    endpoint = Endpoint(url=f"http://127.0.0.1:{stub.port}{path}", headers=headers, timeout_seconds=10)
    with pytest.raises(McpUnavailable, match=reason):
        await McpClient().list_tools(endpoint)


async def test_wrong_path_is_explained():
    async with McpStub(free_port(), token="") as open_stub:  # 不要求鉴权，才能走到 404
        endpoint = Endpoint(url=f"http://127.0.0.1:{open_stub.port}/nope", timeout_seconds=10)
        with pytest.raises(McpUnavailable, match="地址不对（HTTP 404）"):
            await McpClient().list_tools(endpoint)


async def test_unreachable_server_is_explained():
    endpoint = Endpoint(url=f"http://127.0.0.1:{free_port()}/mcp", timeout_seconds=5)
    with pytest.raises(McpUnavailable, match="连不上 MCP 服务"):
        await McpClient().list_tools(endpoint)
