"""models 包的业务异常。message 面向用户，不含内部细节。"""


class ModelsError(Exception):
    pass


class Denied(ModelsError):
    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__(message)


class NotFound(ModelsError):
    def __init__(self, message: str = "对象不存在") -> None:
        super().__init__(message)


class Conflict(ModelsError):
    pass


class Invalid(ModelsError):
    pass


class Unavailable(ModelsError):
    """模型或厂商配置暂时不可用（例如密钥解不开）。"""

    def __init__(self, message: str = "模型暂时不可用") -> None:
        super().__init__(message)
