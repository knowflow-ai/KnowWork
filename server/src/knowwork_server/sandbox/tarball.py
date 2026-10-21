"""给 Docker archive 接口用的 tar 包（只用于 root 才能写的位置：工作区骨架、input、暂存目录）。"""

import io
import tarfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    path: str  # 相对根目录，不以 / 开头
    uid: int
    gid: int
    mode: int
    data: bytes | None = None  # None 表示目录


def build(entries: list[Entry]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            info = tarfile.TarInfo(entry.path)
            info.uid, info.gid, info.mode = entry.uid, entry.gid, entry.mode
            if entry.data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(entry.data)
                archive.addfile(info, io.BytesIO(entry.data))
    return buffer.getvalue()
