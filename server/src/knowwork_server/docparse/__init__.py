"""文档解析服务（MinerU）的运行时配置：管理后台「模型 → 文档解析」里配，会话附件与扩展共用。"""

from .api import build_router
from .models import DOCPARSE_RLS_TABLES, DOCPARSE_TABLES, DocumentParserRow
from .service import DocumentParsers, ParserConfig, ParserSave, ParserTest, ParserView, ProbeView

__all__ = [
    "DOCPARSE_RLS_TABLES",
    "DOCPARSE_TABLES",
    "DocumentParserRow",
    "DocumentParsers",
    "ParserConfig",
    "ParserSave",
    "ParserTest",
    "ParserView",
    "ProbeView",
    "build_router",
]
