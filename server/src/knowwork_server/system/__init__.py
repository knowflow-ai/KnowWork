"""系统设置：品牌（名称、Logo）与系统信息。只有系统管理员能改，品牌对所有人（含未登录）可见。"""

from .api import build_router
from .brand import DEFAULT_NAME, Brand, BrandService, Forbidden, InvalidBrand
from .info import SystemInfo, SystemInfoService
from .models import SYSTEM_TABLES, SystemBrand

__all__ = [
    "DEFAULT_NAME",
    "SYSTEM_TABLES",
    "Brand",
    "BrandService",
    "Forbidden",
    "InvalidBrand",
    "SystemBrand",
    "SystemInfo",
    "SystemInfoService",
    "build_router",
]
