"""附件类型判定：扩展名与文件内容（魔数）双重判断，两者对不上一律拒绝。"""

import io
import json
import zipfile
from dataclasses import dataclass
from enum import StrEnum

from ..sessionfiles.names import extension


class Kind(StrEnum):
    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    TEXT = "text"
    PNG = "png"
    JPEG = "jpeg"


# 附件在沙箱里直接提取文字（pdftotext、pandoc、markitdown，一两秒就好），不经版面模型：Agent 产品读附件的通行做法。
# 旧格式 Word 先在转换沙箱里转成 docx（CONVERTED_KINDS）再提取。
EXTRACTED_KINDS = frozenset({Kind.PDF, Kind.DOC, Kind.DOCX, Kind.PPTX})
CONVERTED_KINDS = frozenset({Kind.DOC})
# 要识别文字（OCR）的：图片，以及提取不出几个字的 PDF（扫描件）。私有化部署的模型多半看不了图，这一步交给 MinerU，
# 没配 MinerU 时图片原样放进沙箱、不解析
OCR_KINDS = frozenset({Kind.PNG, Kind.JPEG})
# 纯文本里是成段文字的（做导读）；CSV、JSON 是数据
PROSE_EXTENSIONS = frozenset({"md", "markdown", "txt"})

EXTENSIONS = {
    "pdf": Kind.PDF, "doc": Kind.DOC, "docx": Kind.DOCX, "pptx": Kind.PPTX, "xlsx": Kind.XLSX,
    "md": Kind.TEXT, "markdown": Kind.TEXT, "txt": Kind.TEXT, "csv": Kind.TEXT, "json": Kind.TEXT,
    "png": Kind.PNG, "jpg": Kind.JPEG, "jpeg": Kind.JPEG,
}
MIME = {
    Kind.PDF: "application/pdf",
    Kind.DOC: "application/msword",
    Kind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    Kind.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    Kind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    Kind.TEXT: "text/plain",
    Kind.PNG: "image/png",
    Kind.JPEG: "image/jpeg",
}
_OOXML_PART = {Kind.DOCX: "word/", Kind.PPTX: "ppt/", Kind.XLSX: "xl/"}
_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE 复合文档（Office 97-2003 的 .doc、.xls、.ppt）
_WORD_STREAM = "WordDocument".encode("utf-16-le")  # .doc 必有的流；目录项里的流名是 UTF-16
_IMAGE_MAGIC = {Kind.PNG: b"\x89PNG\r\n\x1a\n", Kind.JPEG: b"\xff\xd8\xff"}
# 文本附件里出现这些开头，说明其实是二进制文件改了后缀
_BINARY_MAGIC = (_ZIP, b"%PDF-", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\x7fELF", b"MZ")


class AttachmentRejected(Exception):
    """附件不收。code 给程序判断，message 直接展示给用户。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


UNSUPPORTED = ("unsupported_format",
               "格式不支持：仅支持 PDF、Word(doc、docx)、PPT(pptx)、Excel(xlsx)、Markdown、TXT、CSV、JSON、图片(png、jpg)")


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
    elif kind is Kind.DOC:
        _check_legacy_word(data)
    elif kind in _IMAGE_MAGIC:
        if not data.startswith(_IMAGE_MAGIC[kind]):
            raise _mismatch()
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


def _check_legacy_word(data: bytes) -> None:
    """旧格式 Word：OLE 复合文档，里面有 WordDocument 流；.xls、.ppt 改了后缀的对不上。"""
    if not data.startswith(_OLE) or _WORD_STREAM not in data:
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
