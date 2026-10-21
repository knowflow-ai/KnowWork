"""运行配置的错误。"""


class ProfileUnavailable(Exception):
    """编译所需的事实取不到（权限服务、技能或模型目录暂时不可用）。不带内部细节，原因只写日志。"""
