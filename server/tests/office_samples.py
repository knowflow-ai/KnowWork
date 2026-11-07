"""测试用的最小 Office 文件。"""

import io
import zipfile

from openpyxl import Workbook

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)
JPEG = b"\xff\xd8\xff\xe0" + bytes(16)


def ooxml(part: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{part}/document.xml", "<doc/>")
    return buffer.getvalue()


DOCX = ooxml("word")
PPTX = ooxml("ppt")


def legacy_doc(stream: str = "WordDocument") -> bytes:
    """看着像旧格式 Word（.doc）的字节：OLE 复合文档的文件头，目录里有 WordDocument 流（流名是 UTF-16）。
    上传时按内容认类型够用了，转换交给假沙箱。换个流名（Workbook）就是改了后缀的 .xls。"""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(504) + stream.encode("utf-16-le") + bytes(64)


def xlsx(rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
