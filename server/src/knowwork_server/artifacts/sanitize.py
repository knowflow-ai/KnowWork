"""表格产物的公式注入防护：以 = + - @ 开头的单元格一律加前缀转义。

- CSV/TSV：单元格前加 `'`（OWASP 推荐做法）；同时处理制表符、回车开头。纯数字（如 -5、+3.2）不是公式，
  也不会被表格软件当公式执行，原样保留，以免把负数变成文本；
- XLSX（openpyxl）：公式单元格与以这些字符开头的文本单元格都改成文本类型，并打上 quotePrefix
  （等价于用户在 Excel 里先输入一个 `'`），显示的仍是原文，但不会被求值，导出 CSV 后也不会；
  没有需要转义的单元格时原样返回字节，不经 openpyxl 重存（重存会丢掉图表、图片等 openpyxl 不保留的内容）；
- 打开前先看压缩包的条目数与解压后总大小，防 zip 炸弹；打不开的表格不收（失败即拒绝）。
"""

import csv
import io
import re
import zipfile

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

TRIGGERS = ("=", "+", "-", "@")
CSV_TRIGGERS = (*TRIGGERS, "\t", "\r")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
MAX_XLSX_ENTRIES = 10_000
MAX_XLSX_UNCOMPRESSED = 256 * 1024 * 1024


class SpreadsheetRejected(Exception):
    """表格无法检查（损坏、编码不明、疑似压缩炸弹）。message 可直接展示。"""


def _risky(value: str, triggers: tuple[str, ...]) -> bool:
    return value.startswith(triggers) and not _NUMBER.fullmatch(value.strip())


def escape_cell(value: str) -> str:
    return "'" + value if _risky(value, CSV_TRIGGERS) else value


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise SpreadsheetRejected("表格编码无法识别（支持 UTF-8 与 GB18030）")


def sanitize_csv(data: bytes, *, delimiter: str = ",") -> tuple[bytes, bool]:
    """返回（内容, 是否改动）。没有需要转义的单元格时原样返回。"""
    text, encoding = _decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as error:
        raise SpreadsheetRejected("CSV 格式不合法") from error
    escaped = [[escape_cell(cell) for cell in row] for row in rows]
    if escaped == rows:
        return data, False
    out = io.StringIO(newline="")
    csv.writer(out, delimiter=delimiter, lineterminator="\r\n").writerows(escaped)
    prefix = "﻿" if data.startswith(b"\xef\xbb\xbf") else ""
    return (prefix + out.getvalue()).encode("utf-8" if encoding == "utf-8-sig" else encoding), True


def _check_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as error:
        raise SpreadsheetRejected("Excel 文件已损坏") from error
    if len(infos) > MAX_XLSX_ENTRIES or sum(info.file_size for info in infos) > MAX_XLSX_UNCOMPRESSED:
        raise SpreadsheetRejected("Excel 文件解压后过大，无法检查")


def _formula_text(value: object) -> str:
    text = getattr(value, "text", None)  # ArrayFormula 等对象
    return text if isinstance(text, str) else str(value)


def _escape_sheet(sheet: Worksheet) -> bool:
    changed = False
    for row in sheet.iter_rows():
        for cell in row:
            if cell.data_type == "f":
                cell.value = _formula_text(cell.value)
            elif not (isinstance(cell.value, str) and _risky(cell.value, TRIGGERS)):
                continue
            cell.data_type = "s"
            cell.quotePrefix = True
            changed = True
    return changed


def sanitize_xlsx(data: bytes) -> tuple[bytes, bool]:
    _check_archive(data)
    try:
        workbook = load_workbook(io.BytesIO(data), data_only=False)
    except Exception as error:  # openpyxl 对坏文件抛的异常种类很多，统一视为无法检查
        raise SpreadsheetRejected("Excel 文件无法打开") from error
    changed = False
    for sheet in workbook.worksheets:
        if isinstance(sheet, Worksheet):
            changed = _escape_sheet(sheet) or changed
    if not changed:
        return data, False
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue(), True
