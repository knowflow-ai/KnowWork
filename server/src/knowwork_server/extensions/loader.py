"""发现与加载扩展。

- 装了哪些扩展：入口组 knowwork.extensions 里登记的都算「已安装」，迁移按已安装的跑（装了包、表就要齐，不看授权，
  授权过期后数据仍在，续上授权即可用）；
- 启用哪些：授权（入口组 knowwork.license 提供的校验器）里有它的模块才启用；
- 任何一步出错只跳过那个扩展并写明原因，开源功能照常。
"""

import functools
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from .contract import ENTRY_GROUP, VERIFIER_GROUP, Extension
from .license import NO_LICENSE, LicenseState

log = logging.getLogger(__name__)

EntryPoints = Callable[[str], Iterable[EntryPoint]]


def _installed(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


@dataclass(frozen=True)
class Skipped:
    name: str
    reason: str


@dataclass(frozen=True)
class Extensions:
    license: LicenseState
    installed: tuple[Extension, ...] = ()
    active: tuple[Extension, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    @property
    def model_modules(self) -> tuple[str, ...]:
        return tuple(module for ext in self.installed for module in ext.model_modules)

    @property
    def rls_tables(self) -> tuple[str, ...]:
        return tuple(table for ext in self.installed for table in ext.rls_tables)

    @property
    def migration_dirs(self) -> tuple[Path, ...]:
        return tuple(ext.migrations for ext in self.installed if ext.migrations is not None)

    def without(self, extension: Extension, reason: str) -> "Extensions":
        """启动时扩展自己装配失败：从启用里去掉，记下原因。"""
        return Extensions(license=self.license, installed=self.installed,
                          active=tuple(e for e in self.active if e is not extension),
                          skipped=(*self.skipped, Skipped(extension.name, reason)))


NO_EXTENSIONS = Extensions(license=NO_LICENSE)


def verify_license(found: EntryPoints = _installed) -> LicenseState:
    verifiers = list(found(VERIFIER_GROUP))
    if not verifiers:
        return NO_LICENSE
    if len(verifiers) > 1:
        return LicenseState(status="invalid", message="装了不止一个授权校验器，无法确定以哪个为准")
    try:
        loaded = verifiers[0].load()
        # 入口可以指向校验器对象，也可以指向类或工厂函数
        verifier = loaded() if isinstance(loaded, type) or not hasattr(loaded, "verify") else loaded
        state = verifier.verify()
    except Exception:
        log.exception("license verifier %s failed", verifiers[0].name)
        return LicenseState(status="invalid", message="授权校验出错，已按没有授权处理")
    if not isinstance(state, LicenseState):
        return LicenseState(status="invalid", message="授权校验器返回的结果无法识别")
    return state


def _load(entry: EntryPoint) -> Extension:
    extension = entry.load()
    for attribute in ("name", "license_module", "model_modules", "rls_tables", "migrations", "setup_api",
                      "setup_worker", "start", "aclose"):
        if not hasattr(extension, attribute):
            raise TypeError(f"extension {entry.name} has no {attribute}")
    return extension


def _reason(license_state: LicenseState, extension: Extension) -> str:
    if license_state.status == "valid":
        return f"授权里没有「{extension.license_module}」模块"
    return license_state.message


def discover(found: EntryPoints = _installed) -> Extensions:
    license_state = verify_license(found)
    installed: list[Extension] = []
    skipped: list[Skipped] = []
    for entry in found(ENTRY_GROUP):
        try:
            installed.append(_load(entry))
        except Exception:
            log.exception("loading extension %s failed", entry.name)
            skipped.append(Skipped(entry.name, "扩展加载失败，详情见业务服务日志"))
    active = tuple(e for e in installed if license_state.allows(e.license_module))
    skipped += [Skipped(e.name, _reason(license_state, e)) for e in installed if e not in active]
    for item in skipped:
        log.warning("extension %s not enabled: %s", item.name, item.reason)
    return Extensions(license=license_state, installed=tuple(installed), active=active, skipped=tuple(skipped))


@functools.cache
def installed_extensions() -> Extensions:
    """本进程的扩展（只发现一次）。测试里直接调 discover 传入假的入口。"""
    return discover()
