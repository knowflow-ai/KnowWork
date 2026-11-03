"""出站请求日志里遮住查询参数中的令牌与密钥（httpx 在 INFO 级别写完整地址）。"""

import logging

import httpx
import pytest

from knowwork_server.ops import logredact

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_secrets_in_query_strings_are_masked():
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=ww1&corpsecret=S3cr3t-value"
    assert logredact.redact(url) == "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=ww1&corpsecret=***"
    assert logredact.redact("GET https://x.test/u?a=1&access_token=tok&code=abc HTTP") == \
        "GET https://x.test/u?a=1&access_token=***&code=*** HTTP"
    assert logredact.redact("https://x.test/user/list?department_id=1") == "https://x.test/user/list?department_id=1"


async def test_httpx_request_lines_no_longer_carry_the_secret(caplog):
    logredact.install()
    logredact.install()  # 装两次也只有一个
    assert sum(isinstance(f, logredact.QuerySecretFilter) for f in logging.getLogger("httpx").filters) == 1
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"errcode": 0}))
    with caplog.at_level(logging.INFO, logger="httpx"):
        async with httpx.AsyncClient(transport=transport) as http:
            await http.get("https://qyapi.example.test/cgi-bin/gettoken", params={"corpid": "ww1",
                                                                                  "corpsecret": "S3cr3t-value"})
    lines = [record.getMessage() for record in caplog.records if record.name == "httpx"]
    assert lines and all("S3cr3t-value" not in line for line in lines)
    assert "corpsecret=***" in lines[0]
