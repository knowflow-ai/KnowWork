"""初始化向导后端（F-INIT-2、F-INIT-3）。"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.service import AccessService
from .api import build_router
from .models import SETUP_RLS_TABLES, SETUP_TABLES, SystemSetup
from .service import AlreadyInitialized, SetupInconsistent, SetupInProgress, SetupResult, SetupService, SetupStatus


def build_setup_service(sessionmaker: async_sessionmaker, access: AccessService) -> SetupService:
    return SetupService(sessionmaker, access)


__all__ = [
    "SETUP_RLS_TABLES",
    "SETUP_TABLES",
    "AlreadyInitialized",
    "SetupInProgress",
    "SetupInconsistent",
    "SetupResult",
    "SetupService",
    "SetupStatus",
    "SystemSetup",
    "build_router",
    "build_setup_service",
]
