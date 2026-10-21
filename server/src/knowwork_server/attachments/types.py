"""附件类型判定：扩展名与文件内容（魔数）双重判断，两者对不上一律拒绝。"""

import io
import json
import zipfile
from dataclasses import dataclass
from enum import StrEnum

from ..sessionfiles.names import extension


class Kind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    TEXT = "text"


# 交给 MinerU 解析成 Markdown 的类型；其余原样放进沙箱
PARSED_KINDS = frozenset({Kind.PDF, Kind.DOCX, Kind.PPTX})

EXTENSIONS = {
    "pdf": Kind.PDF, "docx": Kind.DOCX, "pptx": Kind.PPTX, "xlsx": Kind.XLSX,
    "md": Kind.TEXT, "markdown": Kind.TEXT, "txt": Kind.TEXT, "csv": Kind.TEXT, "json": Kind.TEXT,
}
MIME = {
    Kind.PDF: "application/pdf",
    Kind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    Kind.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    Kind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    Kind.TEXT: "text/plain",
}
_OOXML_PART = {Kind.DOCX: "word/", Kind.PPTX: "ppt/", Kind.XLSX: "xl/"}
_ZIP = b"PK\x03\x04"
# 文本附件里出现这些开头，说明其实是二进制文件改了后缀
_BINARY_MAGIC = (_ZIP, b"%PDF-", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\x7fELF", b"MZ")


class AttachmentRejected(Exception):
    """附件不收。code 给程序判断，message 直接展示给用户。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


UNSUPPORTED = ("unsupported_format", "格式不支持：仅支持 PDF、Word(docx)、PPT(pptx)、Excel(xlsx)、Markdown、TXT、CSV、JSON")


@dataclass(frozen=True)
class Detected:
    kind: Kind
    mime: str


def detect(filename: str, data: bytes) -> Detected:
    kind = EXTENSIONS.get(extension(filename))
    if kind is None:
        raise AttachmentRejected(*UNSUPPORTED)
    if not data:
        raise AttachmentRejected("empty_file", "文件是空的")
    if kind is Kind.PDF:
        _check_pdf(data)
    elif kind in _OOXML_PART:
        _check_ooxml(kind, data)
    else:
        _check_text(filename, data)
    return Detected(kind=kind, mime=MIME[kind])


def _mismatch() -> AttachmentRejected:
    return AttachmentRejected("type_mismatch", "文件内容与扩展名不符")


def _check_pdf(data: bytes) -> None:
    if b"%PDF-" not in data[:1024]:
        raise _mismatch()


def _check_ooxml(kind: Kind, data: bytes) -> None:
    if not data.startswith(_ZIP):
        raise _mismatch()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, ValueError, OSError) as error:
        raise AttachmentRejected("file_corrupt", "文件已损坏，无法打开") from error
    if "[Content_Types].xml" not in names:
        raise AttachmentRejected("file_corrupt", "文件已损坏，无法打开")
    if not any(name.startswith(_OOXML_PART[kind]) for name in names):
        raise _mismatch()


def decode_text(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _check_text(filename: str, data: bytes) -> None:
    if data.startswith(_BINARY_MAGIC) or b"\x00" in data[:8192]:
        raise _mismatch()
    text = decode_text(data)
    if text is None:
        raise AttachmentRejected("file_corrupt", "文本无法识别编码（支持 UTF-8 与 GB18030）")
    if extension(filename) == "json":
        try:
            json.loads(text)
        except ValueError as error:
            raise AttachmentRejected("file_corrupt", "JSON 文件内容不合法") from error
