"""会话文件存储与文件名处理。"""

from urllib.parse import unquote

import boto3
import pytest
from botocore.stub import Stubber

from knowwork_server.sessionfiles import (
    InvalidFileName,
    clean_name,
    content_disposition,
    local_session_stores,
    s3_session_stores,
    unique_name,
)
from knowwork_server.sessionfiles.names import extension, is_plain_name, replace_extension
from knowwork_server.skills.blobstore import sha256_hex
from knowwork_server.skills.errors import BlobIntegrityError

pytestmark = pytest.mark.anyio


async def test_local_sessions_are_isolated_and_deletable(tmp_path):
    stores = local_session_stores(tmp_path)
    one, two = stores("default", "c_1"), stores("default", "c_2")
    digest = await one.put(b"hello")
    assert await one.get(digest, max_bytes=10) == b"hello"
    with pytest.raises(BlobIntegrityError):
        await two.get(digest, max_bytes=10)  # 另一个会话的命名空间里没有
    await one.delete(digest)
    await one.delete(digest)  # 幂等
    with pytest.raises(BlobIntegrityError):
        await one.get(digest, max_bytes=10)


@pytest.mark.parametrize("bad", [("../x", "c"), ("default", "c/../../x"), ("", "c"), ("default", "")])
def test_namespace_segments_are_validated(tmp_path, bad):
    with pytest.raises(ValueError):
        local_session_stores(tmp_path)(*bad)


async def test_s3_session_prefix_and_delete():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b",
                          endpoint_url="http://s3.invalid")
    digest = sha256_hex(b"x")
    key = f"session-files/default/c_1/sha256/{digest[:2]}/{digest}"
    store = s3_session_stores(client, "bkt")("default", "c_1")
    with Stubber(client) as stub:
        stub.add_response("delete_object", {}, {"Bucket": "bkt", "Key": key})
        stub.add_client_error("delete_object", service_error_code="AccessDenied", http_status_code=403)
        await store.delete(digest)
        with pytest.raises(BlobIntegrityError):
            await store.delete(digest)
        stub.assert_no_pending_responses()


def test_s3_without_prefix():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b",
                          endpoint_url="http://s3.invalid")
    store = s3_session_stores(client, "bkt", prefix="")("w", "c")
    assert store._key("0" * 64).startswith("w/c/sha256/")


@pytest.mark.parametrize(("raw", "clean"), [
    ("报告.pdf", "报告.pdf"), ("C:\\Users\\a\\合同.docx", "合同.docx"), ("../../etc/passwd", "passwd"),
    ("a\x00b\x1f.txt", "ab.txt"), ('a<b>:"c|?*.md', "a_b___c___.md"), ("  空格.txt  ", "空格.txt"),
])
def test_clean_name(raw, clean):
    assert clean_name(raw) == clean


@pytest.mark.parametrize("raw", ["", None, ".", "..", ".env", "dir/", "\x00"])
def test_clean_name_rejects(raw):
    with pytest.raises(InvalidFileName):
        clean_name(raw)


def test_long_names_keep_extension():
    name = clean_name("长" * 300 + ".xlsx")
    assert len(name) == 120 and name.endswith(".xlsx")
    assert len(clean_name("x" * 300)) == 120


def test_helpers():
    assert extension("a.B.PDF") == "pdf" and extension("noext") == "" and extension(".x") == ""
    assert replace_extension("报告.pdf", "md") == "报告.md" and replace_extension("readme", "md") == "readme.md"
    assert is_plain_name("a.txt") and not is_plain_name("sub/a.txt") and not is_plain_name(".hidden")


def test_unique_name():
    assert unique_name("a.md", []) == "a.md"
    assert unique_name("a.md", ["A.MD"]) == "a (2).md"
    assert unique_name("a.md", ["a.md", "a (2).md"]) == "a (3).md"
    assert unique_name("readme", ["readme"]) == "readme (2)"
    with pytest.raises(InvalidFileName):
        unique_name("a", ["a", *[f"a ({i})" for i in range(2, 10_000)]])


def test_content_disposition_handles_chinese_and_quotes():
    value = content_disposition('季度"报告";v2.xlsx')
    assert value.startswith('attachment; filename="')
    fallback = value.split('filename="')[1].split('"')[0]
    assert fallback.isascii() and '"' not in fallback and ";" not in fallback
    assert unquote(value.split("filename*=UTF-8''")[1]) == '季度"报告";v2.xlsx'
    assert content_disposition("") .startswith('attachment; filename="download"')
