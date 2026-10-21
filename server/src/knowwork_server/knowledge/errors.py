"""知识模块的业务异常。message 面向用户（也会交给模型），不含内部细节。"""


class KnowledgeError(Exception):
    """所有知识异常的基类。code 是稳定错误码，供接口与前端使用。"""

    code = "knowledge_error"
    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InvalidInput(KnowledgeError):
    code = "invalid_input"
    status = 422


class NotFound(KnowledgeError):
    code = "not_found"
    status = 404

    def __init__(self, message: str = "知识源不存在") -> None:
        super().__init__(message)


class AccessDenied(KnowledgeError):
    code = "forbidden"
    status = 403

    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__(message)


class IdentityUnavailable(KnowledgeError):
    """调用者在知识源侧没有可用身份：没有对应账号、没配连接密钥、Key 失效、KnowFlow 不认签名断言。"""

    code = "identity_unavailable"
    status = 403


class ScopeDenied(KnowledgeError):
    """调用者在知识源侧对绑定范围一个都读不了。"""

    code = "scope_denied"
    status = 403


class SourceUnavailable(KnowledgeError):
    """知识源暂时不可用或返回了无法识别的结果。"""

    code = "source_unavailable"
    status = 502


class NoReadableKnowledge(KnowledgeError):
    """本轮绑定了知识，但调用者一个都读不了（第 5.4 节）。message 里带各知识源的原因。"""

    code = "no_readable_knowledge"
    status = 403
