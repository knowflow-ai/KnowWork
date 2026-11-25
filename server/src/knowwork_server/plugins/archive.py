"""上传的插件压缩包（.tgz）的检查：能不能当 DSH 的插件包装上，用到了哪些能力。只读压缩包，不执行里面的任何代码。

插件包是 DSH 官方的 bundle 形态：一个 npm 包，package.json 里用 dsh.bundle.patch 指向一份配置补丁（声明要加载哪些
插件）。布局同 npm pack 的产物：所有内容在唯一的顶层目录下（通常叫 package/）。依赖要自己打进包里
（bundledDependencies）：装的时候解开即用，不联网、不跑包管理器。

限制：压缩包不超过 100MB、解开后不超过 400MB、条目不超过 3 万个；只收普通文件和目录（符号链接、硬链接、设备文件
一律拒绝）；拒绝绝对路径和越出顶层目录的路径。

官方包（@deepseek-ai/ 开头的）不用打进包里，但要写在 peerDependencies 里：宿主按这份声明把它们接到自己那一份上
（实测：没声明的，插件放在宿主目录以外时导入失败、整个插件加载不了）。上传时查一遍，漏了当场说明。

静态扫描只看插件自己的代码（不看它带的 node_modules），按关键字找它用到的能力，结果给上传的人和管理员看：只提示、不拦。
压过或混淆过的代码可能扫不全，所以它不是安全边界；边界是插件跑在哪（个人的宿主容器里）。

插件页上给人看的标题、一句话说明、说明文档、组件清单，照 DSH 官方插件页（dsh-client-ui-plugin-manager、
dsh-plugin-detail，0.2.2-rc.5）的取法从包里读，同样只读文件、不执行代码：
- 标题、说明取语言文件 locale/<语言>.json 的 meta.title、meta.description（先中文后英文），没有就用 package.json 的
  description 和包名；
- 说明文档：先中文后英文，哪个语言文件的 meta.detail 指了一份 Markdown 就用它，都没指就用包里的 README；指了但不合规矩
  或文件不在的，官方是报读取失败、不退回 README，我们同样不显示（不拦上传）；
- 组件清单：配置补丁里插入的那几行（cordis:group 分组展开），用包本身的就用上面的标题和说明，别的只写模块名
  （官方对子路径插件读它导出的语言文件，要执行代码才知道，这里不做）。
"""

import hashlib
import io
import json
import posixpath
import re
import tarfile
from dataclasses import dataclass

import yaml

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
MAX_ENTRIES = 30_000
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SCANNED_FILE_BYTES = 2 * 1024 * 1024
MAX_SCANNED_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 500
MAX_TITLE_CHARS = 80
MAX_DOC_BYTES = 256 * 1024  # 单个语言文件、说明文档、补丁文件
MAX_DOCS_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DETAIL_CHARS = 64 * 1024  # 存库的说明文档，超了截断
MAX_COMPONENTS = 64
LANGUAGES = ("zh", "en")  # 界面只有中文；官方的语言链最后是英文
_README = ("README.md", "readme.md", "Readme.md", "README.markdown", "README")
# 要留下来读的文本：语言文件、Markdown、补丁（YAML）、没有后缀的 README
_DOC = re.compile(r"^(?:locale/[A-Za-z0-9-]+\.json|.+\.(?:md|markdown|ya?ml)|README)$")
_DETAIL_PATH = re.compile(r"^[^\\:]+\.(?:md|markdown)$")

# npm 包名：可带 @scope/，小写字母、数字、点、下划线、连字符
_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,48}/)?[a-z0-9][a-z0-9._-]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]{0,40}$")
_CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")

# 能力 → 命中的写法。键的顺序就是界面上的顺序
_CAPABILITIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fs", re.compile(r"""['"](?:node:)?fs(?:/promises)?['"]""")),
    ("process", re.compile(r"""['"](?:node:)?(?:child_process|worker_threads|cluster)['"]""")),
    ("network", re.compile(r"""['"](?:node:)?(?:https?|http2|net|dgram|tls|dns)['"]|\bfetch\s*\(|\bWebSocket\b"""
                           r"""|['"](?:undici|axios|node-fetch|got|ws)['"]""")),
    ("env", re.compile(r"\bprocess\.env\b")),
    ("llm-stream", re.compile(r"""['"]llm/stream['"]""")),
)
CAPABILITY_KEYS = tuple(key for key, _ in _CAPABILITIES)
# 代码里导入的官方包：import … from '@deepseek-ai/x'、import('@deepseek-ai/x/y')、require('@deepseek-ai/x')
_OFFICIAL_IMPORT = re.compile(r"""(?:from\s*|import\s*\(?\s*|require\s*\(\s*)['"](@deepseek-ai/[a-z0-9][a-z0-9._-]*)""")
_BUNDLED_OFFICIAL = re.compile(r"^node_modules/(@deepseek-ai/[a-z0-9][a-z0-9._-]*)/")


class ArchiveInvalid(ValueError):
    """压缩包不能当插件装；message 直接给人看。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Component:
    """组件清单里的一行：包里配置补丁插入的一个插件。"""

    id: str
    module: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class InspectedArchive:
    name: str
    version: str
    description: str
    digest: str  # 整个压缩包的 SHA-256，对象存储与宿主上的缓存目录都按它寻址
    size: int
    capabilities: tuple[str, ...]
    title: str = ""  # 作者在语言文件里写的标题；没写为空，界面上显示包名
    detail: str = ""  # 说明文档（Markdown）
    components: tuple[Component, ...] = ()


def _safe(raw: str) -> str:
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ArchiveInvalid("unsafe_path", "压缩包里有不安全的路径")
    # 带 .. 的一律不收（不先规整再判断：package/../x 规整后看着在里面，解开时却落在插件目录外）
    if ".." in raw.split("/"):
        raise ArchiveInvalid("unsafe_path", "压缩包里的路径越出了插件目录")
    normalized = posixpath.normpath(raw)
    if normalized in ("", "."):
        raise ArchiveInvalid("unsafe_path", "压缩包里有不安全的路径")
    return normalized


def _manifest(raw: bytes) -> dict:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveInvalid("invalid_manifest", "package.json 不是合法的 JSON") from error
    if not isinstance(manifest, dict):
        raise ArchiveInvalid("invalid_manifest", "package.json 不是合法的 JSON")
    return manifest


def _identity(manifest: dict) -> tuple[str, str, str, str]:
    name, version = manifest.get("name"), manifest.get("version")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ArchiveInvalid("invalid_name", "package.json 里的 name 不是合法的包名")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ArchiveInvalid("invalid_version", "package.json 里的 version 要写成 1.2.3 这样的版本号")
    dsh = manifest.get("dsh")
    bundle = dsh.get("bundle") if isinstance(dsh, dict) else None
    patch = bundle.get("patch") if isinstance(bundle, dict) else None
    if not isinstance(patch, str) or not patch.strip():
        raise ArchiveInvalid("not_a_bundle", "这不是 DSH 的插件包：package.json 里没有 dsh.bundle.patch")
    description = manifest.get("description")
    return name, version, _safe(patch), (description if isinstance(description, str) else "")[:MAX_DESCRIPTION_CHARS]


def inspect_archive(data: bytes) -> InspectedArchive:
    """同步、吃 CPU：调用方放到线程里跑。"""
    if not data:
        raise ArchiveInvalid("empty_archive", "上传的文件是空的")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveInvalid("archive_too_large", f"压缩包超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)}MB 上限")
    try:
        return _inspect(data)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ArchiveInvalid("invalid_archive", "上传的文件不是合法的 .tgz 压缩包") from error


def _inspect(data: bytes) -> InspectedArchive:
    roots: set[str] = set()
    paths: set[str] = set()
    manifest: dict | None = None
    found: set[str] = set()
    imported: set[str] = set()
    bundled: set[str] = set()
    docs: dict[str, str] = {}
    unpacked = scanned = entries = kept = 0
    # 流式读（r|gz）：一边解压一边看条目，不把解开的内容留在内存里；解开的总量按条目头里声明的大小先算、超了就停
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|gz") as archive:
        for member in archive:
            entries += 1
            if entries > MAX_ENTRIES:
                raise ArchiveInvalid("too_many_files", f"压缩包里的条目超过 {MAX_ENTRIES} 个")
            path = _safe(member.name)
            root, _, inner = path.partition("/")
            roots.add(root)
            if len(roots) > 1:
                raise ArchiveInvalid("unexpected_layout", "压缩包里只能有一个顶层目录（用 npm pack 打出来的就是）")
            if member.isdir():
                continue
            if not member.isfile():
                raise ArchiveInvalid("link_not_allowed", "压缩包里不能有符号链接、硬链接或设备文件；依赖请打成普通文件")
            unpacked += member.size
            if unpacked > MAX_UNPACKED_BYTES:
                raise ArchiveInvalid("unpacked_too_large", f"解开后超过 {MAX_UNPACKED_BYTES // (1024 * 1024)}MB 上限")
            paths.add(inner)
            own = _BUNDLED_OFFICIAL.match(inner)
            if own:
                bundled.add(own.group(1))
            if inner == "package.json":
                if member.size > MAX_MANIFEST_BYTES:
                    raise ArchiveInvalid("invalid_manifest", "package.json 太大")
                manifest = _manifest(_read(archive, member))
            elif _keepable(inner, member.size) and kept + member.size <= MAX_DOCS_TOTAL_BYTES:
                kept += member.size
                docs[inner] = _read(archive, member).decode("utf-8", errors="replace")
            elif _scannable(inner, member.size) and scanned + member.size <= MAX_SCANNED_TOTAL_BYTES:
                scanned += member.size
                text = _read(archive, member).decode("utf-8", errors="ignore")
                found.update(key for key, pattern in _CAPABILITIES if key not in found and pattern.search(text))
                imported.update(_OFFICIAL_IMPORT.findall(text))
    if manifest is None:
        raise ArchiveInvalid("missing_manifest", "压缩包里没有 package.json（要在顶层目录下）")
    name, version, patch, description = _identity(manifest)
    if patch not in paths:
        raise ArchiveInvalid("missing_patch", f"package.json 里 dsh.bundle.patch 指向的 {patch} 不在压缩包里")
    peers = manifest.get("peerDependencies")
    missing = sorted(imported - bundled - set(peers if isinstance(peers, dict) else ()))
    if missing:
        raise ArchiveInvalid("undeclared_peer", "代码里用到的官方包没有写进 package.json 的 peerDependencies，装上后会加载失败："
                             + "、".join(missing[:5]))
    title, description = _presentation(docs, name, description)
    return InspectedArchive(name=name, version=version, description=description,
                            digest=hashlib.sha256(data).hexdigest(), size=len(data),
                            capabilities=tuple(key for key in CAPABILITY_KEYS if key in found),
                            title=title, detail=_detail(docs),
                            components=_components(docs.get(patch), name, title, description))


def _scannable(inner: str, size: int) -> bool:
    return (inner.endswith(_CODE_SUFFIXES) and size <= MAX_SCANNED_FILE_BYTES
            and "node_modules/" not in f"/{inner}")


def _read(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    return handle.read() if handle is not None else b""


def _keepable(inner: str, size: int) -> bool:
    return size <= MAX_DOC_BYTES and "node_modules/" not in f"/{inner}" and bool(_DOC.fullmatch(inner))


def _meta(docs: dict[str, str], language: str) -> dict:
    """locale/<语言>.json 的 meta；没有或不合法时为空。"""
    raw = docs.get(f"locale/{language}.json")
    if raw is None:
        return {}
    try:
        meta = json.loads(raw).get("meta")
    except (json.JSONDecodeError, AttributeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _presentation(docs: dict[str, str], name: str, fallback: str) -> tuple[str, str]:
    """标题和一句话说明：语言文件里先中文后英文，说明最后退回 package.json 的 description。"""
    metas = [_meta(docs, language) for language in LANGUAGES]
    title = next((text for meta in metas if (text := _text(meta.get("title"), MAX_TITLE_CHARS))), "")
    description = next((text for meta in metas if (text := _text(meta.get("description"), MAX_DESCRIPTION_CHARS))),
                       fallback)
    return title, description


def _detail_path(value: object) -> str | None:
    if not isinstance(value, str) or not _DETAIL_PATH.fullmatch(value):
        return None
    parts = value.split("/")
    return None if any(part in ("", ".", "..") for part in parts) else value


def _detail(docs: dict[str, str]) -> str:
    """说明文档：先中文后英文，语言文件的 meta.detail 指的那份；都没指就用 README。"""
    for language in LANGUAGES:
        meta = _meta(docs, language)
        if "detail" in meta:
            path = _detail_path(meta["detail"])
            return _clean(docs[path]) if path and path in docs else ""
    return next((_clean(docs[name]) for name in _README if name in docs), "")


def _clean(text: str) -> str:
    """去掉开头的 BOM 和 YAML 头（官方同样去掉），太长的截断。"""
    text = text.removeprefix("\ufeff")
    if text.startswith(("---\n", "---\r\n")):
        end = re.search(r"\r?\n---[ \t]*(?:\r?\n|$)", text[3:])
        if end:
            text = text[3 + end.end():]
    return text.strip()[:MAX_DETAIL_CHARS]


def _rows(items: object, depth: int = 0) -> list[tuple[str, str]]:
    """补丁里插入的插件行：(id, 模块名)；分组（cordis:group）展开它下面的行。"""
    found: list[tuple[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        module = item.get("name")
        if module == "cordis:group" or item.get("group") is True:
            if depth < 4:
                found.extend(_rows(item.get("config"), depth + 1))
        elif isinstance(module, str) and module:
            row_id = item.get("id")
            found.append((row_id[:64] if isinstance(row_id, str) else "", module[:200]))
    return found


def _components(patch: str | None, name: str, title: str, description: str) -> tuple[Component, ...]:
    if not patch:
        return ()
    try:
        operations = yaml.safe_load(patch)
    except yaml.YAMLError:
        return ()
    rows = [row for operation in (operations if isinstance(operations, list) else [])
            if isinstance(operation, dict) for row in _rows(operation.get("insert"))]
    return tuple(
        Component(id=row_id, module=module, title=title or name, description=description) if module == name
        else Component(id=row_id, module=module, title=module)
        for row_id, module in rows[:MAX_COMPONENTS])
