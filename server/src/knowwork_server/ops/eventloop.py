"""进程入口用的事件循环：装了 uvloop（随 uvicorn[standard] 装上）就用它，没装退回标准库的。

uvicorn 只在 Server.run() 里换 uvloop；我们的入口自己 asyncio.run，再调 serve()（公开、内部两个端口共用一个循环），
所以装了也一直没用上。2026-11-09 压测（模拟模型、100 个会话同时发问）：换上后业务服务每轮的 CPU 少了约一成。
"""

import asyncio
from collections.abc import Callable


def loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """给 asyncio.run(..., loop_factory=...)；None 就是标准库的事件循环。"""
    try:
        import uvloop
    except ImportError:
        return None
    return uvloop.new_event_loop
