"""对象存储：按摘要寻址、读出校验。S3 实现用 botocore 官方 Stubber 当桩，不连真实服务。"""

import io

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from knowwork_server.skills.blobstore import LocalBlobStore, S3BlobStore, make_s3_client, sha256_hex
from knowwork_server.skills.errors import BlobIntegrityError

DATA = b"skill archive bytes"
DIGEST = sha256_hex(DATA)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_local_roundtrip_and_idempotent(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert await store.put(DATA) == DIGEST
    assert await store.put(DATA) == DIGEST
    assert await store.exists(DIGEST)
    assert await store.get(DIGEST, max_bytes=1024) == DATA
    assert not any(p.name.startswith(".tmp-") for p in tmp_path.rglob("*"))


@pytest.mark.anyio
async def test_local_detects_tampering_missing_and_oversize(tmp_path):
    store = LocalBlobStore(tmp_path)
    await store.put(DATA)
    (tmp_path / "sha256" / DIGEST[:2] / DIGEST).write_bytes(b"tampered")
    with pytest.raises(BlobIntegrityError):
        await store.get(DIGEST, max_bytes=1024)
    with pytest.raises(BlobIntegrityError):
        await store.get("0" * 64, max_bytes=1024)
    other = await store.put(b"x" * 100)
    with pytest.raises(BlobIntegrityError):
        await store.get(other, max_bytes=10)
    assert not await store.exists("1" * 64)


@pytest.mark.anyio
@pytest.mark.parametrize("bad", ["../../etc/passwd", "ABC", "", "g" * 64])
async def test_digest_format_checked(tmp_path, bad):
    with pytest.raises(BlobIntegrityError):
        await LocalBlobStore(tmp_path).get(bad, max_bytes=10)


def _client():
    return make_s3_client(endpoint_url="http://rustfs.invalid:9000", access_key="test-access", secret_key="test-secret")


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


KEY = f"skills/sha256/{DIGEST[:2]}/{DIGEST}"


@pytest.mark.anyio
async def test_s3_put_writes_once():
    client = _client()
    store = S3BlobStore(client, "kw-bucket")
    with Stubber(client) as stub:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                              expected_params={"Bucket": "kw-bucket", "Key": KEY})
        stub.add_response("put_object", {}, {"Bucket": "kw-bucket", "Key": KEY, "Body": DATA,
                                             "ContentType": "application/octet-stream",
                                             "Metadata": {"sha256": DIGEST}})
        stub.add_response("head_object", {"ContentLength": len(DATA)}, {"Bucket": "kw-bucket", "Key": KEY})
        assert await store.put(DATA) == DIGEST
        assert await store.put(DATA) == DIGEST  # 已存在：只 head，不再 put
        stub.assert_no_pending_responses()


@pytest.mark.anyio
async def test_s3_get_verifies_digest():
    client = _client()
    store = S3BlobStore(client, "kw-bucket")
    with Stubber(client) as stub:
        stub.add_response("get_object", {"Body": _body(DATA), "ContentLength": len(DATA)},
                          {"Bucket": "kw-bucket", "Key": KEY})
        stub.add_response("get_object", {"Body": _body(b"tampered"), "ContentLength": 8},
                          {"Bucket": "kw-bucket", "Key": KEY})
        stub.add_response("get_object", {"Body": _body(DATA), "ContentLength": len(DATA)},
                          {"Bucket": "kw-bucket", "Key": KEY})
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        assert await store.get(DIGEST, max_bytes=1024) == DATA
        with pytest.raises(BlobIntegrityError):
            await store.get(DIGEST, max_bytes=1024)
        with pytest.raises(BlobIntegrityError):
            await store.get(DIGEST, max_bytes=4)
        with pytest.raises(BlobIntegrityError):
            await store.get(DIGEST, max_bytes=1024)


@pytest.mark.anyio
async def test_s3_errors_become_store_errors():
    client = _client()
    store = S3BlobStore(client, "kw-bucket", prefix="")
    with Stubber(client) as stub:
        stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
        with pytest.raises(BlobIntegrityError):
            await store.put(DATA)
        stub.add_client_error("head_object", service_error_code="NotFound", http_status_code=404,
                              expected_params={"Bucket": "kw-bucket", "Key": f"sha256/{DIGEST[:2]}/{DIGEST}"})
        assert not await store.exists(DIGEST)


def test_s3_requires_credentials_and_bucket():
    with pytest.raises(ValueError):
        make_s3_client(endpoint_url="http://x", access_key="", secret_key="")
    with pytest.raises(ValueError):
        S3BlobStore(_client(), "")


@pytest.mark.anyio
async def test_s3_ensure_bucket_creates_only_when_missing():
    client = _client()
    store = S3BlobStore(client, "kw-bucket")
    with Stubber(client) as stub:
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404,
                              expected_params={"Bucket": "kw-bucket"})
        stub.add_response("create_bucket", {}, {"Bucket": "kw-bucket"})
        stub.add_response("head_bucket", {}, {"Bucket": "kw-bucket"})
        await store.ensure_bucket()
        await store.ensure_bucket()  # 已存在：不再建
        stub.assert_no_pending_responses()


@pytest.mark.anyio
async def test_s3_ensure_bucket_reports_other_errors():
    client = _client()
    store = S3BlobStore(client, "kw-bucket")
    with Stubber(client) as stub:
        stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403,
                              expected_params={"Bucket": "kw-bucket"})
        with pytest.raises(BlobIntegrityError):
            await store.ensure_bucket()
