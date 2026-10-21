"""技能包的业务异常。message 面向用户，不含内部细节；code 是稳定错误码，供前端与测试判定。"""


class SkillError(Exception):
    status = 400

    def __init__(self, code: str, message: str, *, file: str = "", field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.file = file
        self.field = field

    def detail(self) -> dict[str, str]:
        raw = {"code": self.code, "message": self.message, "file": self.file, "field": self.field}
        return {key: value for key, value in raw.items() if value}


class PackageInvalid(SkillError):
    """ZIP 或 SKILL.md 不合格，安装被拒绝。"""

    status = 422


class SkillNotFound(SkillError):
    status = 404

    def __init__(self, message: str = "技能不存在") -> None:
        super().__init__("skill_not_found", message)


class SkillForbidden(SkillError):
    status = 403

    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__("forbidden", message)


class SkillConflict(SkillError):
    status = 409


class BlobIntegrityError(Exception):
    """存储里的内容与摘要不符，或读不到。只记日志，对外统一说存储不可用。"""
