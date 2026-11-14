"""几个脚本共用的小工具：解包、打包、命名空间。不是给模型直接调的。"""

import os
import shutil
import tempfile
import zipfile

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}


def q(prefix, name):
    return f"{{{NS[prefix]}}}{name}"


def unpack(path, target):
    """解压，但拒绝符号链接与越出目录的条目——外来的文件不可信。"""

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = os.path.normpath(info.filename)
            if name.startswith("..") or os.path.isabs(name):
                raise SystemExit(f"压缩包里有越出目录的条目：{info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # 符号链接
            archive.extract(info, target)


def pack(directory, path):
    """从目录里面打包。[Content_Types].xml 放第一个，和 Office 写出来的一致。"""

    if os.path.exists(path):
        os.remove(path)
    names = []
    for root, _, files in os.walk(directory):
        for file in files:
            full = os.path.join(root, file)
            names.append(os.path.relpath(full, directory))
    names.sort(key=lambda n: (n != "[Content_Types].xml", n))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(os.path.join(directory, name), name)


class Workdir:
    """传进来的是目录就直接用；是文件就解到临时目录，用完按需打包回去。"""

    def __init__(self, path, output=None):
        self.path = path
        self.output = output
        self.is_dir = os.path.isdir(path)
        self.dir = path if self.is_dir else tempfile.mkdtemp(prefix="ooxml-")

    def __enter__(self):
        if not self.is_dir:
            unpack(self.path, self.dir)
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None and not self.is_dir:
                pack(self.dir, self.output or self.path)
        finally:
            if not self.is_dir:
                shutil.rmtree(self.dir, ignore_errors=True)
