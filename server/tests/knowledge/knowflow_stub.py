"""KnowFlow 测试桩：按身份返回不同结果，扮演 RAGFlow SDK 接口与断言校验。

- 身份：X-KnowFlow-Assertion（用参考实现 AssertionVerifier 校验，按邮箱找用户）或 Authorization: Bearer <个人 Key>；
- 权限：每个用户能读的知识库集合；请求里有读不了的知识库就 403 并列出 denied_resources（与 api_key_gate 一致）。
"""

import json
from dataclasses import dataclass, field

import httpx

from knowwork_server.knowledge.assertion import AssertionRejected, AssertionVerifier, MemoryReplayStore, key_id

SERVICE_KEY = "svc-" + "k" * 40


@dataclass
class StubUser:
    id: str
    email: str
    readable: set[str]
    disabled: bool = False


def chunk(dataset_id: str, doc_id: str, n: int, text: str | None = None, page: int = 1) -> dict:
    return {"id": f"{doc_id}-c{n}", "content": text or f"{doc_id} 第{n}段", "document_id": doc_id,
            "document_keyword": f"{doc_id}.pdf", "dataset_id": dataset_id, "similarity": 0.9 - n / 100,
            "positions": [[page, 10, 200, 30 + n, 60 + n]]}


@dataclass
class KnowFlowStub:
    users: list[StubUser]
    api_keys: dict[str, str] = field(default_factory=dict)  # key -> email
    chunks: dict[str, list[dict]] = field(default_factory=dict)  # dataset_id -> 检索命中
    documents: dict[str, list[dict]] = field(default_factory=dict)  # document_id -> 文档内全部片段
    # dataset_id -> 嵌入模型；没写的按同一个默认模型。一次检索混用不同模型时照真实 KnowFlow 报 102
    embedding_models: dict[str, str] = field(default_factory=dict)
    chunk_counts: dict[str, int] = field(default_factory=dict)  # document_id -> 文档信息里的分块数（不填按实有）
    now: float | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False
    assertion_supported: bool = True  # False：照没装断言插件的 KnowFlow，忽略断言头

    def __post_init__(self) -> None:
        self.verifier = AssertionVerifier({key_id(SERVICE_KEY): SERVICE_KEY}, replay_store=MemoryReplayStore())

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down")
        user = self._identify(request)
        if isinstance(user, httpx.Response):
            return user
        path = request.url.path
        if path == "/api/v1/retrieval":
            return self._retrieval(user, json.loads(request.content))
        if path == "/api/v1/datasets":
            return _ok([{"id": d, "name": f"库{d}", "embedding_model": self._model(d)} for d in sorted(user.readable)])
        parts = path.split("/")
        if len(parts) >= 6 and parts[3] == "datasets" and parts[5] == "documents":
            return self._documents(user, parts, request)
        return httpx.Response(404, json={"code": 404, "message": "not found"})

    def _identify(self, request: httpx.Request):
        assertion = request.headers.get("x-knowflow-assertion") if self.assertion_supported else None
        if assertion:
            try:
                claims = self.verifier.verify(assertion, now=self.now)
            except AssertionRejected as error:
                return httpx.Response(401, json={"code": 401, "error_code": error.code, "message": error.code})
            return self._by_email(claims.email)
        auth = request.headers.get("authorization", "")
        if not auth:  # RAGFlow token_required：没有 Authorization 时 HTTP 200、code 0、data false
            return httpx.Response(200, json={"code": 0, "data": False, "message": "`Authorization` can't be empty"})
        email = self.api_keys.get(auth.removeprefix("Bearer "))
        if email is None:
            return httpx.Response(200, json={"code": 109, "data": False,
                                             "message": "Authentication error: API key is invalid!"})
        return self._by_email(email)

    def _by_email(self, email: str):
        user = next((u for u in self.users if u.email == email), None)
        if user is None:
            return httpx.Response(401, json={"code": 401, "error_code": "assertion_user_not_found"})
        if user.disabled:
            return httpx.Response(401, json={"code": 401, "error_code": "assertion_user_disabled"})
        return user

    def _retrieval(self, user: StubUser, body: dict) -> httpx.Response:
        denied = [d for d in body["dataset_ids"] if d not in user.readable]
        if denied:
            return httpx.Response(403, json={"message": "denied", "data": {"denied_resources": denied}})
        if len({self._model(d) for d in body["dataset_ids"]}) > 1:
            return _ok_code(102, "Datasets use different embedding models.")
        hits = [c for d in body["dataset_ids"] for c in self.chunks.get(d, [])]
        return _ok({"chunks": hits[: body.get("page_size", 30)], "total": len(hits), "doc_aggs": []})

    def _model(self, dataset_id: str) -> str:
        return self.embedding_models.get(dataset_id, "bge-m3")

    def _documents(self, user: StubUser, parts: list[str], request: httpx.Request) -> httpx.Response:
        dataset_id = parts[4]
        if dataset_id not in user.readable:
            return httpx.Response(403, json={"message": "denied", "data": {"denied_resources": [dataset_id]}})
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params.get("page_size", 30))
        if len(parts) == 6:
            docs = sorted({c["document_id"] for c in self.chunks.get(dataset_id, [])})
            window = docs[(page - 1) * size: page * size]
            return _ok({"docs": [{"id": d, "name": f"{d}.pdf"} for d in window], "total": len(docs)})
        document_id = parts[6]
        all_chunks = self.documents.get(document_id, [])
        window = all_chunks[(page - 1) * size: page * size]
        count = self.chunk_counts.get(document_id, len(all_chunks))
        return _ok({"chunks": window, "doc": {"id": document_id, "name": f"{document_id}.pdf", "chunk_count": count},
                    "total": len(all_chunks)})


def _ok(data) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data})


def _ok_code(code: int, message: str) -> httpx.Response:
    return httpx.Response(200, json={"code": code, "message": message})
