"""技能 ZIP 包的安装校验。

限制：包不超过 100MB、解压后不超过 200MB、文件不超过 256 个、
单条压缩比不超过 200；拒绝路径穿越、绝对路径、符号链接、加密条目、重复路径；
`SKILL.md` 只能在根目录或唯一的一级目录下，且全包只能有一个技能。

先看中央目录里声明的大小再读内容，声明与实际不符时 zipfile 自己会报 CRC/长度错误。
"""

import hashlib
import io
import posixpath
import stat
import zipfile
from dataclasses import dataclass

from .errors import PackageInvalid
from .manifest import SkillManifest, parse_skill_md
from .workflow_def import FILE as WORKFLOW_FILE
from .workflow_def import MAX_BYTES as MAX_WORKFLOW_BYTES
from .workflow_def import WorkflowDefinition, parse_workflow

IGNORED_PREFIXES = ("__MACOSX/",)


@dataclass(frozen=True)
class PackageLimits:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_files: int = 256
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class PackageFile:
    path: str  # 相对技能根目录
    size: int
    sha256: str


@dataclass(frozen=True)
class ValidatedPackage:
    manifest: SkillManifest
    archive: bytes
    archive_digest: str  # 整个 ZIP 的 SHA-256，对象存储按它寻址
    content_digest: str  # SKILL.md 正文的 SHA-256，宿主加载时据此校验
    root_prefix: str
    files: tuple[PackageFile, ...]
    workflow: WorkflowDefinition | None = None  # 技能根目录下可选的 workflow.yaml


def _safe_path(raw: str) -> str:
    value = raw or ""
    first = value.split("/", 1)[0]
    if not value or "\x00" in value or "\\" in value or value.startswith("/") or ":" in first:
        raise PackageInvalid("unsafe_archive_path", "ZIP 里有不安全的路径", file=value[:200])
    normalized = posixpath.normpath(value)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise PackageInvalid("unsafe_archive_path", "ZIP 里的路径越出了技能目录", file=value[:200])
    return normalized


def _open(content: bytes, limits: PackageLimits) -> zipfile.ZipFile:
    if not content:
        raise PackageInvalid("empty_archive", "上传的 ZIP 是空的")
    if len(content) > limits.max_archive_bytes:
        raise PackageInvalid("archive_too_large", f"ZIP 超过 {limits.max_archive_bytes} 字节上限")
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as error:
        raise PackageInvalid("invalid_zip", "上传的文件不是合法的 ZIP") from error


def _check_entry(info: zipfile.ZipInfo, path: str, limits: PackageLimits) -> None:
    if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
        raise PackageInvalid("symlink_not_allowed", "不允许符号链接", file=path)
    if info.flag_bits & 0x1:
        raise PackageInvalid("encrypted_archive", "不支持加密的 ZIP 条目", file=path)
    if info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
        raise PackageInvalid("suspicious_compression", "ZIP 条目的压缩比异常", file=path)


def _entries(archive: zipfile.ZipFile, limits: PackageLimits) -> list[tuple[zipfile.ZipInfo, str]]:
    entries: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    total = 0
    for info in archive.infolist():
        path = _safe_path(info.filename)
        if info.filename.startswith(IGNORED_PREFIXES) or info.is_dir():
            continue
        _check_entry(info, path, limits)
        if path in seen:
            raise PackageInvalid("duplicate_archive_path", "ZIP 里有重复的路径", file=path)
        seen.add(path)
        total += info.file_size
        entries.append((info, path))
    if len(entries) > limits.max_files:
        raise PackageInvalid("too_many_files", f"ZIP 里的文件超过 {limits.max_files} 个")
    if total > limits.max_uncompressed_bytes:
        raise PackageInvalid("expanded_archive_too_large", f"解压后超过 {limits.max_uncompressed_bytes} 字节上限")
    return entries


def _skill_root(entries: list[tuple[zipfile.ZipInfo, str]]) -> tuple[zipfile.ZipInfo, str]:
    roots = [(info, path) for info, path in entries if path == "SKILL.md" or path.endswith("/SKILL.md")]
    if not roots:
        raise PackageInvalid("missing_skill_md", "ZIP 里没有 SKILL.md")
    if len(roots) != 1:
        raise PackageInvalid("multiple_skills", "一个 ZIP 只能包含一个技能", file=", ".join(p for _, p in roots[:5]))
    info, path = roots[0]
    prefix = path[: -len("SKILL.md")]
    if "/" in prefix.rstrip("/"):
        raise PackageInvalid("invalid_bundle_structure", "SKILL.md 只能在根目录或唯一的一级目录下", file=path)
    outside = next((p for _, p in entries if prefix and not p.startswith(prefix)), None)
    if outside is not None:
        raise PackageInvalid("invalid_bundle_structure", "技能目录之外还有文件", file=outside)
    return info, prefix


def _read_skill_md(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    try:
        return archive.read(info).decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageInvalid("skill_md_not_utf8", "SKILL.md 必须是 UTF-8 编码", file="SKILL.md") from error


def _read_workflow(archive: zipfile.ZipFile, entries: list[tuple[zipfile.ZipInfo, str]], prefix: str,
                   manifest: SkillManifest) -> WorkflowDefinition | None:
    info = next((info for info, path in entries if path == f"{prefix}{WORKFLOW_FILE}"), None)
    if info is None:
        return None
    if info.file_size > MAX_WORKFLOW_BYTES:
        raise PackageInvalid("invalid_workflow", f"workflow.yaml 不能超过 {MAX_WORKFLOW_BYTES} 字节", file=WORKFLOW_FILE)
    try:
        text = archive.read(info).decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageInvalid("invalid_workflow", "workflow.yaml 必须是 UTF-8 编码", file=WORKFLOW_FILE) from error
    return parse_workflow(text, manifest)


def validate_skill_zip(content: bytes, limits: PackageLimits | None = None) -> ValidatedPackage:
    limits = limits or PackageLimits()
    with _open(content, limits) as archive:
        try:
            entries = _entries(archive, limits)
            skill_info, prefix = _skill_root(entries)
            manifest = parse_skill_md(_read_skill_md(archive, skill_info))
            workflow = _read_workflow(archive, entries, prefix, manifest)
            files = tuple(PackageFile(path=path[len(prefix):], size=info.file_size,
                                      sha256=hashlib.sha256(archive.read(info)).hexdigest())
                          for info, path in entries)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError) as error:
            raise PackageInvalid("invalid_zip", "ZIP 内容损坏") from error
    return ValidatedPackage(manifest=manifest, archive=content, archive_digest=hashlib.sha256(content).hexdigest(),
                            content_digest=hashlib.sha256(manifest.body.encode()).hexdigest(),
                            root_prefix=prefix, files=files, workflow=workflow)
