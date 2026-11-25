"""工作区路径：对话里的文件只有一个身份——相对这段对话工作区的路径。

同一个工作区在不同地方挂在不同位置（挂载点）：宿主看到的 `/sessions/<对话>`、会话沙箱里的 `/workspace`，扩展还有自己的
（例如 Office 编辑器容器里的 `/work/<对话>`）。规矩：
- 模型写的路径只在入口解析一次（parse），认三种写法：相对路径（按工作区根）、`/workspace/...`、`/sessions/<这段对话>/...`；
- 之后一律传 WorkPath，进数据库的也是它（产物的 source_path）；
- 要真实路径时按挂载点换（Mount.at），真实路径换回来用 Mount.of；功能代码不拼路径。

只做字面判定，是工具层的约定，不是安全边界：沙箱里命令与文件操作默认以 root 运行，中间路径上的符号链接照常解析，
模型用 bash 本来就能读写容器里任何地方。跨会话、跨租户与宿主机的隔离只靠容器本身。
"""

import posixpath
from dataclasses import dataclass

MAX_PATH = 4096
MAX_NAME_BYTES = 255
SESSIONS_DIR = "/sessions"

# InvalidPath（沙箱错误的一种）定义在文件末尾，见那里的说明


def _checked(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > MAX_PATH:
        raise InvalidPath("路径不合法")
    return raw


def _normal(absolute: str) -> str:
    # posixpath.normpath 会留下开头的「//」（POSIX 允许它有别的含义），这里一律当一个「/」
    return "/" + posixpath.normpath(absolute).lstrip("/")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


@dataclass(frozen=True)
class WorkPath:
    """相对工作区根的路径：POSIX 写法，不以「/」开头、不含「.」「..」与空段；根是空串。只经 parse、Mount.of、child 得到。"""

    relative: str

    @property
    def is_root(self) -> bool:
        return self.relative == ""

    @property
    def name(self) -> str:
        return posixpath.basename(self.relative)

    def within(self, folder: "WorkPath") -> bool:
        """在这个文件夹里（或就是它）。"""
        return folder.is_root or self.relative == folder.relative or self.relative.startswith(folder.relative + "/")

    def child(self, name: str) -> "WorkPath":
        """文件夹里的一个文件（名字按 check_name 校验）。"""
        clean = check_name(name)
        return WorkPath(clean if self.is_root else f"{self.relative}/{clean}")

    def __str__(self) -> str:
        return self.relative


ROOT = WorkPath("")
# 附件目录：系统放入，模型只读（写文件工具、在线编辑都不改它）
INPUT = WorkPath("input")


@dataclass(frozen=True)
class Mount:
    """工作区挂在哪个真实位置（绝对路径）。"""

    root: str

    def at(self, path: WorkPath) -> str:
        return self.root if path.is_root else f"{self.root}/{path.relative}"

    def of(self, physical: str, *, outside: str = "只能访问本会话工作目录下的路径") -> WorkPath:
        """这个挂载点下的真实路径换回工作区路径；不是绝对路径、越出挂载点的拒绝（outside 是给人看的原因）。"""
        if not _checked(physical).startswith("/"):
            raise InvalidPath("路径不合法")
        normal = _normal(physical)
        if not _inside(normal, self.root):
            raise InvalidPath(outside)
        return WorkPath(normal[len(self.root):].lstrip("/"))

    def holds(self, physical: str) -> bool:
        return isinstance(physical, str) and physical.startswith("/") and _inside(_normal(physical), self.root)


# 会话沙箱里的工作区
SANDBOX = Mount("/workspace")


def host_mount(conversation_id: str) -> Mount:
    """宿主看到的会话工作目录。DSH 的文件系统服务只认路径，路径里带会话才分得清是哪个容器；
    容器里同一路径是指向 /workspace 的符号链接，所以命令里写绝对路径也照样能用。"""
    return Mount(f"{SESSIONS_DIR}/{conversation_id}")


def parse(raw: str, conversation_id: str | None = None) -> WorkPath:
    """模型写的路径 → 工作区路径。相对路径按工作区根；绝对路径认 /workspace/...，给了对话时还认 /sessions/<这段对话>/...；
    别的绝对路径、越出工作区的一律拒绝。"""
    _checked(raw)
    if not raw.startswith("/"):
        return SANDBOX.of(posixpath.join(SANDBOX.root, raw), outside="只能访问 /workspace 下的路径")
    if conversation_id is not None and _inside(_normal(raw), SESSIONS_DIR):
        return host_mount(conversation_id).of(raw)
    return SANDBOX.of(raw, outside="只能访问 /workspace 下的路径")


def decode(raw: str) -> WorkPath:
    """跨进程传来的工作区路径（相对写法，根是空串；例如宿主的沙箱请求）：规范化后不越出工作区才算数。"""
    if raw == "":
        return ROOT
    _checked(raw)
    if raw.startswith("/"):
        raise InvalidPath("路径不合法")
    return SANDBOX.of(posixpath.join(SANDBOX.root, raw), outside="只能访问本会话工作目录下的路径")


def writable(path: WorkPath) -> WorkPath:
    """写文件工具不往附件目录里写（约定：附件由系统放入，模型只读不改），也不能把工作区根写成文件。"""
    if path.is_root:
        raise InvalidPath("该路径是目录，不能写成文件")
    if path.within(INPUT):
        raise InvalidPath("/workspace/input 是附件目录，不能写入")
    return path


def check_name(name: str) -> str:
    """单个文件名（附件目录里的、工作区里新建的）：不含路径分隔符与控制字符。"""
    if (not isinstance(name, str) or name in ("", ".", "..") or "/" in name
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or len(name.encode("utf-8")) > MAX_NAME_BYTES):
        raise InvalidPath("文件名不合法")
    return name


# 放在最后导入：sandbox 包初始化时（containers.py、docker_sandbox.py）要从这里取 SANDBOX、INPUT 等，先导入本模块的一方
# （例如单独跑事件投影的测试）若在开头就导入 sandbox，会绕回这个还没定义完的模块。上面的函数都是调用时才用 InvalidPath
from .sandbox.contract import SandboxError


class InvalidPath(SandboxError):
    """路径不合法或越出工作区。沙箱的内部接口把它回成 403（宿主据此说没有权限），所以算沙箱错误的一种。"""
