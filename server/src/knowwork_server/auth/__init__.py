"""调用身份：所有业务模块只依赖这里的 Principal，不关心身份从哪种凭证来。"""

from .principal import AuthMethod, Principal

__all__ = ["AuthMethod", "Principal"]
