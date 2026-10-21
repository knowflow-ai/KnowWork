"""文档预览与（以后的）在线编辑：会话里文件的查看方式都在这里，产物与附件的存取仍归各自模块。

当前只有预览：Word、PPT 第一次预览时在会话沙箱里用原生 LibreOffice 转成 PDF，存进会话存储并记在产物上，
之后直接取；PDF 原样返回。表格、Markdown、文本与图片由前端用下载接口拿到原文件自己渲染（照 DSH 官方预览的组合）。
"""

from .api import build_router
from .preview import PreviewService

__all__ = ["PreviewService", "build_router"]
