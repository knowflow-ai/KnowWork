"""MCP 管理的错误。message 面向用户，不含内部细节；status 是 HTTP 状态。"""


class McpError(Exception):
    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InvalidInput(McpError):
    status = 400


class Forbidden(McpError):
    status = 403

    def __init__(self, message: str = "没有权限管理 MCP 服务") -> None:
        super().__init__(message)


class NotFound(McpError):
    status = 404

    def __init__(self, message: str = "MCP 服务不存在") -> None:
        super().__init__(message)


class Conflict(McpError):
    status = 409


class Unreachable(McpError):
    """保存或刷新时连不上 MCP 服务，什么都不改。"""

    status = 422
