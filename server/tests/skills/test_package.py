"""技能 ZIP 包安装校验。"""

import hashlib
import io
import stat
import zipfile

import pytest

from knowwork_server.skills.errors import PackageInvalid
from knowwork_server.skills.package import PackageLimits, validate_skill_zip

SKILL_MD = "---\nname: contract-extract\ndescription: 抽取合同字段\n---\n\n按步骤抽取。\n"


def make_zip(files: dict[str, bytes | str], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return buffer.getvalue()


def test_valid_root_package():
    data = make_zip({"SKILL.md": SKILL_MD, "scripts/run.py": "print(1)"})
    package = validate_skill_zip(data)
    assert package.manifest.name == "contract-extract"
    assert package.archive_digest == hashlib.sha256(data).hexdigest()
    assert package.root_prefix == ""
    assert {f.path for f in package.files} == {"SKILL.md", "scripts/run.py"}
    assert package.content_digest == hashlib.sha256(package.manifest.body.encode()).hexdigest()


def test_valid_single_top_level_directory_and_macos_junk_ignored():
    data = make_zip({"contract-extract/SKILL.md": SKILL_MD, "contract-extract/ref.md": "x",
                     "__MACOSX/contract-extract/._SKILL.md": "junk"})
    package = validate_skill_zip(data)
    assert package.root_prefix == "contract-extract/"
    assert {f.path for f in package.files} == {"SKILL.md", "ref.md"}


def _symlink_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD)
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def _encrypted_flag_zip() -> bytes:
    # zipfile 写入时会重置标志位，所以写完后直接改中央目录里 secret.txt 那条的「加密」位
    data = bytearray(make_zip({"SKILL.md": SKILL_MD, "secret.txt": "x"}))
    entry = data.rfind(b"PK\x01\x02")
    assert data[entry + 46:entry + 56] == b"secret.txt"
    data[entry + 8] |= 0x1
    return bytes(data)


@pytest.mark.parametrize("build,code", [
    (lambda: b"", "empty_archive"),
    (lambda: b"not a zip at all", "invalid_zip"),
    (lambda: make_zip({"../evil/SKILL.md": SKILL_MD}), "unsafe_archive_path"),
    (lambda: make_zip({"/abs/SKILL.md": SKILL_MD}), "unsafe_archive_path"),
    (lambda: make_zip({"a\\b": "x", "SKILL.md": SKILL_MD}), "unsafe_archive_path"),
    (lambda: make_zip({"C:/x": "x", "SKILL.md": SKILL_MD}), "unsafe_archive_path"),
    (_symlink_zip, "symlink_not_allowed"),
    (_encrypted_flag_zip, "encrypted_archive"),
    (lambda: make_zip({"README.md": "x"}), "missing_skill_md"),
    (lambda: make_zip({"a/SKILL.md": SKILL_MD, "b/SKILL.md": SKILL_MD}), "multiple_skills"),
    (lambda: make_zip({"a/b/SKILL.md": SKILL_MD}), "invalid_bundle_structure"),
    (lambda: make_zip({"a/SKILL.md": SKILL_MD, "other.txt": "x"}), "invalid_bundle_structure"),
    (lambda: make_zip({"SKILL.md": b"\xff\xfe\x00bad"}), "skill_md_not_utf8"),
    (lambda: make_zip({"SKILL.md": "no frontmatter"}), "missing_frontmatter"),
])
def test_rejections(build, code):
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(build())
    assert caught.value.code == code


def test_duplicate_paths_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD)
        with pytest.warns(UserWarning):
            archive.writestr("SKILL.md", SKILL_MD)
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(buffer.getvalue())
    assert caught.value.code == "duplicate_archive_path"


def test_limits():
    small = PackageLimits(max_archive_bytes=2000, max_uncompressed_bytes=3000, max_files=3, max_compression_ratio=200)
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(make_zip({"SKILL.md": SKILL_MD, "big.bin": b"x" * 5000}, compression=zipfile.ZIP_STORED),
                           small)
    assert caught.value.code == "archive_too_large"
    loose = PackageLimits(max_archive_bytes=10_000, max_uncompressed_bytes=3000, max_files=3, max_compression_ratio=10**6)
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(make_zip({"SKILL.md": SKILL_MD, "big.txt": "a" * 5000}), loose)
    assert caught.value.code == "expanded_archive_too_large"
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(make_zip({"SKILL.md": SKILL_MD, **{f"f{i}": "x" for i in range(4)}}), small)
    assert caught.value.code == "too_many_files"


def test_compression_ratio_bomb_rejected():
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(make_zip({"SKILL.md": SKILL_MD, "bomb.txt": "0" * 2_000_000}))
    assert caught.value.code == "suspicious_compression"


def test_default_limits_match_benchmark():
    limits = PackageLimits()
    assert limits.max_archive_bytes == 100 * 1024 * 1024
    assert limits.max_uncompressed_bytes == 200 * 1024 * 1024
    assert limits.max_files == 256
    assert limits.max_compression_ratio == 200
