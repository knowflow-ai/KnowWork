"""access 包的业务异常。message 面向用户，不含内部细节。"""

from .vocab import InvalidInput


class AccessError(Exception):
    pass


class AccessDenied(AccessError):
    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__(message)


class NotFound(AccessError):
    def __init__(self, message: str = "对象不存在") -> None:
        super().__init__(message)


class Conflict(AccessError):
    pass


__all__ = ["AccessDenied", "AccessError", "Conflict", "InvalidInput", "NotFound"]
