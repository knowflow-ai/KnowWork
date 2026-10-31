"""工作区路径与文件名校验。

只做字面判定，是工具层的约定，不是安全边界：命令与文件操作默认以 root 运行，
中间路径上的符号链接照常解析，模型用 bash 本来就能读写容器里任何地方。跨会话、跨租户与宿主机
的隔离只靠容器本身。
"""

import posixpath

from .contract import INPUT_DIR, SandboxError

WORKSPACE_DIR = "/workspace"
# 宿主看到的会话工作目录：/sessions/<会话 id>。DSH 的文件系统服务只认路径，路径里带会话才分得清是哪个容器；
# 容器里同一路径是指向 /workspace 的符号链接，所以命令里写绝对路径也照样能用。
SESSIONS_DIR = "/sessions"
MAX_PATH = 4096
MAX_NAME_BYTES = 255


class InvalidPath(SandboxError):
    pass


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def resolve(path: str) -> str:
    """相对路径按 /workspace 解析；结果必须落在 /workspace 下。"""
    if not isinstance(path, str) or not path.strip() or "\x00" in path or len(path) > MAX_PATH:
        raise InvalidPath("路径不合法")
    joined = path if path.startswith("/") else posixpath.join(WORKSPACE_DIR, path)
    normal = "/" + posixpath.normpath(joined).lstrip("/")
    if not _inside(normal, WORKSPACE_DIR):
        raise InvalidPath("只能访问 /workspace 下的路径")
    return normal


def session_root(session_id: str) -> str:
    return f"{SESSIONS_DIR}/{session_id}"


def from_session_path(path: str, session_id: str) -> str:
    """宿主路径（/sessions/<本会话>/...）换成容器里的 /workspace/...；别的会话或会话目录外的路径一律拒绝。"""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or len(path) > MAX_PATH:
        raise InvalidPath("路径不合法")
    normal = posixpath.normpath(path)
    root = session_root(session_id)
    if not _inside(normal, root):
        raise InvalidPath("只能访问本会话工作目录下的路径")
    return WORKSPACE_DIR + normal[len(root):]


def resolve_writable(path: str) -> str:
    """写文件工具不往附件目录里写（约定：附件由系统放入，模型只读不改）。"""
    normal = resolve(path)
    if normal == WORKSPACE_DIR:
        raise InvalidPath("该路径是目录，不能写成文件")
    if _inside(normal, INPUT_DIR):
        raise InvalidPath("/workspace/input 是附件目录，不能写入")
    return normal


def check_name(name: str) -> str:
    """input/output 目录下的单个文件名：不含路径分隔符与控制字符。"""
    if (not isinstance(name, str) or name in ("", ".", "..") or "/" in name
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or len(name.encode("utf-8")) > MAX_NAME_BYTES):
        raise InvalidPath("文件名不合法")
    return name
