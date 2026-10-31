"""技能的环境变量：谁管这一层谁填值，技能在会话里加载以后，它跑的命令才带上这些值。

- 谁填：企业技能系统管理员、空间技能对它有 owner 的人（空间管理员）、个人技能本人，只在本人的会话里用；
- 存法：{名字: 值} 加密成一段存在技能上（AES-256-GCM，附加数据绑定空间与技能 id，挪到别的行解不开），只写不读，
  接口只给已经填了哪些名字。只能填技能声明过的名字；值跟着技能走，换版本不丢；复制到上一层时不带；
- 注入：宿主执行命令时报上这个会话加载过哪些技能，业务服务按此刻的会话清单核对（不在清单里的不认），取这些技能
  声明的变量的值加到这条命令的环境里；同名时上层技能的优先。宿主里不经手这些值（DSH 里不放密钥）；
- 命令的输出交回给模型之前，把这些值替换成 ******：模型 echo、printenv 看到的不是原文。写进文件的挡不住，
  技能作者要自己别把凭据写进产物。
"""

import json
from typing import Any

from ..knowledge.crypto import SecretBox, SecretError
from .errors import PackageInvalid
from .models import Skill

PURPOSE = "skill-env-secret"
MAX_VALUE = 4096


def declared(row: Skill) -> set[str]:
    """草稿与发布版本里声明过的变量名（草稿里新加的也能先填好）。"""
    return {str(spec.get("name")) for manifest in (row.draft_manifest, row.published_manifest or {})
            for spec in manifest.get("env", ()) if spec.get("name")}


class EnvBox:
    def __init__(self, box: SecretBox) -> None:
        self._box = box

    @classmethod
    def from_master_key(cls, master_key: str) -> "EnvBox":
        return cls(SecretBox.from_master_key(master_key, PURPOSE))

    @staticmethod
    def _context(row: Skill) -> str:
        return f"skill_env:{row.workspace_id}:{row.id}"

    def read(self, row: Skill) -> dict[str, str]:
        if not row.env_ciphertext:
            return {}
        try:
            values = json.loads(self._box.open(row.env_ciphertext, context=self._context(row)))
        except (SecretError, ValueError):
            return {}  # 换了主密钥或被改过：当作没填，重新填一次就好
        return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}

    def write(self, row: Skill, values: dict[str, str]) -> None:
        row.env_ciphertext = self._box.seal(json.dumps(values, ensure_ascii=False), context=self._context(row)) \
            if values else None
        row.env_filled = sorted(values)

    def change(self, row: Skill, changes: dict[str, Any]) -> None:
        """changes：名字 → 新值（空串或 None 是清掉）。只能是技能声明过的名字。"""
        names = declared(row)
        unknown = [name for name in changes if name not in names]
        if unknown:
            raise PackageInvalid("env_undeclared", f"技能没有声明环境变量 {min(unknown)}", field="env")
        values = self.read(row)
        for name, value in changes.items():
            if value is None or value == "":
                values.pop(name, None)
                continue
            if not isinstance(value, str) or len(value) > MAX_VALUE or "\x00" in value:
                raise PackageInvalid("invalid_env_value", f"环境变量 {name} 的值不合法（最长 {MAX_VALUE} 个字符）",
                                     field="env")
            values[name] = value
        self.write(row, values)
