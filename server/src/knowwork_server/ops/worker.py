"""worker 容器的启动入口（占位）。

后台任务（附件解析、产物处理、清理）的队列消费者由对应功能线接入；在那之前，本入口只校验配置可加载、
保持进程存活并在 SIGTERM 时退出，让一条命令部署与容器编排先跑通。
用法：`python -m knowwork_server.ops.worker`
"""

import asyncio
import contextlib
import logging
import signal

log = logging.getLogger(__name__)


async def run(stop: asyncio.Event | None = None) -> None:
    from ..config import get_settings

    get_settings()  # 配置不完整立即失败，不带病运行
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    log.info("worker started; no task queue consumers are registered yet")
    await stop.wait()
    log.info("worker stopping")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
