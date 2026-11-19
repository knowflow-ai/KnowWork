"""本机开发的 Office 文件（LocalOfficeFiles）：放进 files_root/<对话>/...，不越出这个目录。"""

import pytest

from knowwork_server.office.files import LocalOfficeFiles
from knowwork_server.workpath import ROOT, InvalidPath, parse

pytestmark = pytest.mark.anyio


async def test_local_files_stay_under_the_root(tmp_path):
    """本机开发目录是 files_root/<对话> 这个挂载点：工作区路径只经 parse 得到（越界的写法在那里就被拒），
    工作区根放不了文件，对话标识不对、符号链接指到外面也不行。"""
    files = LocalOfficeFiles(tmp_path)
    for raw in ("../x.docx", "a/../../x.docx", "/etc/x.docx"):
        with pytest.raises(InvalidPath):
            parse(raw)
    assert await files.place(None, "c1", parse("a//b.docx"), b"x") == str(tmp_path.resolve() / "c1" / "a" / "b.docx")
    with pytest.raises(Exception):  # noqa: B017
        await files.place(None, "c1", ROOT, b"x")
    with pytest.raises(Exception):  # noqa: B017
        await files.place(None, "../c1", parse("a.docx"), b"x")
    (tmp_path / "c1" / "out").symlink_to(tmp_path.parent)
    with pytest.raises(Exception):  # noqa: B017
        await files.place(None, "c1", parse("out/x.docx"), b"x")


