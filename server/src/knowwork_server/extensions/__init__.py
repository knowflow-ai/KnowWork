"""扩展加载：扩展模块经 Python 入口组挂进来，这里不知道它们是什么，只按授权决定加载哪些。"""

from .contract import ENTRY_GROUP, VERIFIER_GROUP, ApiContext, Extension, WorkerContext
from .license import NO_LICENSE, LicenseState, LicenseVerifier
from .loader import NO_EXTENSIONS, Extensions, Skipped, discover, installed_extensions, verify_license

__all__ = [
    "ENTRY_GROUP",
    "NO_EXTENSIONS",
    "NO_LICENSE",
    "VERIFIER_GROUP",
    "ApiContext",
    "Extension",
    "Extensions",
    "LicenseState",
    "LicenseVerifier",
    "Skipped",
    "WorkerContext",
    "discover",
    "installed_extensions",
    "verify_license",
]
