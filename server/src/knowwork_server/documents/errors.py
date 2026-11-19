"""读、存对话文件出错时给人看的说明与建议的状态码：预览接口（api.py）和扩展的编辑入口（editing.py）共用一张表。"""

from ..artifacts.service import ArtifactNotFound, ArtifactUnavailable
from ..sandbox.contract import SandboxError, SandboxTooLarge
from .files import FileGone, FileMissing, FileOutside, FilesOnDevice
from .preview import PreviewUnsupported

# 按顺序匹配：子类在前（SandboxTooLarge 是 SandboxError 的子类）
_TABLE: tuple[tuple[tuple[type[Exception], ...], int, str | None], ...] = (
    ((FilesOnDevice,), 409, "这段对话在创建它的电脑上执行，文件在那台电脑上"),
    ((FileOutside,), 400, "只能读这段对话工作目录里的文件"),
    ((FileMissing,), 404, "找不到这个文件，可能已经移走或删掉了"),
    ((FileGone,), 410, "文件已随执行环境回收，交付过的文件仍可在交付卡片里打开"),
    ((ArtifactNotFound, ArtifactUnavailable), 410, "文件内容已不可用"),
    ((PreviewUnsupported,), 415, "这种文件不提供 PDF 预览"),
    ((SandboxTooLarge,), 413, "文件太大，不能在这里打开"),
    ((SandboxError,), 503, None),  # 说明带上原因
)


def describe(error: Exception) -> tuple[int, str] | None:
    """认得的错误给出（状态码, 说明）；认不得的是 None，由调用方照常抛出。"""
    for kinds, status, message in _TABLE:
        if isinstance(error, kinds):
            return status, message if message is not None else f"文件暂时读不了：{error}"
    return None
