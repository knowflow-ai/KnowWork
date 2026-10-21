"""测试用的最小 Office 文件。"""

import io
import zipfile

from openpyxl import Workbook

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def ooxml(part: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{part}/document.xml", "<doc/>")
    return buffer.getvalue()


DOCX = ooxml("word")
PPTX = ooxml("ppt")


def xlsx(rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
