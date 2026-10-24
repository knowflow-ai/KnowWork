"""ModelCatalog：厂商与模型的统一入口（管理接口、网关取路由、业务服务自己调模型都经它）。

模型对空间成员一律可用：2026-10-23 去掉了按模型分组授权（对话路径从没按它拦过，宿主总是请求别名 default），
以后要按人收窄再加。
"""

from .catalog import ModelMixin


class ModelCatalog(ModelMixin):
    """管理厂商与模型要空间管理员；按别名或能力取模型不按人收窄。"""
