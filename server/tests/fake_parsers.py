"""只看环境变量的解析服务配置：附件测试不起管理后台的配置表时用。生产上由 docparse.DocumentParsers 统一给，
它在管理后台没配时同样退回这几个环境变量。"""

from types import SimpleNamespace

from knowwork_server.attachments import AttachmentSettings


class EnvParsers:
    def __init__(self, settings: AttachmentSettings) -> None:
        self._settings = settings

    async def current(self, workspace_id: str) -> SimpleNamespace | None:
        settings = self._settings
        if not settings.mineru_configured:
            return None
        return SimpleNamespace(base_url=settings.mineru_base_url, api_key=None, lang=settings.mineru_lang,
                               max_concurrency=settings.mineru_max_concurrency,
                               server_url=settings.mineru_server_url or None)
