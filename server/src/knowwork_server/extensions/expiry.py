"""运行中授权到期就停：到期那一刻进程自己退出，由部署的重启策略拉起来（compose 里是 unless-stopped）。

重启后校验到已过期，扩展不再启用，和「过期以后重启」走的是同一条路：扩展的路由、工具、后台任务、登录方式都在启动时装配，
运行中逐个拆下来容易漏。以前要等下一次重启才停，客户的服务一直不重启就一直能用。

只在到期那一刻醒一次（授权的到期日按当天结束算，就是次日零点，服务器时间），不轮询。醒来先对一下时间，中途改过系统时间、
还没真到期就接着等剩下的；到期了先重读授权：客户已经换上续期的授权、启用中的扩展都还在里面，就接着跑到新的到期日，不重启。
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from .license import LicenseState
from .loader import Extensions, verify_license

log = logging.getLogger(__name__)

# 进程因授权到期退出时的退出码（EX_TEMPFAIL）：不是 0，按「失败才重启」配置的进程管理器也会把它拉起来
EXIT_LICENSE_ENDED = 75


async def _stopped(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), max(seconds, 0.0))
        return True
    except TimeoutError:
        return False


async def stop_when_license_ends(extensions: Extensions, stop: asyncio.Event, *,
                                 reverify: Callable[[], LicenseState] = verify_license,
                                 clock: Callable[[], datetime] = datetime.now) -> bool:
    """授权到期又没续上：置 stop 并返回 True，调用方据此以 EXIT_LICENSE_ENDED 退出。

    没有启用收费的扩展、永久授权、stop 先被置位（正常停机）都返回 False。clock 与授权里的到期时间一样按服务器本地时间。"""
    # 不要授权模块的扩展不随授权到期停，只盯收费的
    modules = {extension.license_module for extension in extensions.active if extension.license_module is not None}
    until = extensions.license.valid_until
    if not modules:
        return False
    while until is not None:
        now = clock()
        if now < until:
            if await _stopped(stop, (until - now).total_seconds()):
                return False
            continue
        state = reverify()
        if state.status == "valid" and all(state.allows(module) for module in modules) \
                and (state.valid_until is None or state.valid_until > now):
            log.info("license renewed, now valid until %s", state.valid_until or "forever")
            until = state.valid_until
            continue
        log.warning("license ended at %s (%s); exiting so the restart disables the extensions", until, state.message)
        stop.set()
        return True
    return False
