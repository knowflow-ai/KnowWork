"""出站请求日志里遮住查询参数中的令牌与密钥。

httpx 在 INFO 级别把每个请求的完整地址写进日志。有的接口只能把凭据放在查询参数里：企业微信取令牌要
`gettoken?corpsecret=...`、之后每个接口带 `access_token=...`；通用 OAuth2 也可以配成令牌放查询参数。
不遮住的话，密钥和令牌会原样留在服务日志里。业务服务与 worker 配日志时装上（install）。
"""

import logging
import re

SECRET_PARAMS = re.compile(
    r"(?i)([?&](?:access_token|corpsecret|client_secret|appsecret|app_secret|secret|token|code)=)[^&\s\"']+")
LOGGERS = ("httpx",)


def redact(text: str) -> str:
    return SECRET_PARAMS.sub(r"\1***", text)


class QuerySecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install() -> None:
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QuerySecretFilter) for f in logger.filters):
            logger.addFilter(QuerySecretFilter())
