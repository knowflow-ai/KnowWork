"""生产入口：`python -m knowwork_server`。

在同一个进程里起两个 uvicorn：公开端口（浏览器与 SDK）与内部端口（只给宿主回调）；扩展提供在线编辑器、又配了
编辑器端口时再起第三个（编辑器单独的来源，config.editor_port），
共享一个运行时容器；运行时在对外服务之前启动、在各个服务都退出之后停止（即进程级 lifespan）。
授权运行中到期又没续上时，像收到停止信号一样停下，以 EXIT_LICENSE_ENDED 退出，重启后扩展不再启用（extensions/expiry.py）。

当前限制：事件泵、实时广播与待写缓冲都在进程内，所以只能单进程部署，不能开多个 worker
或多副本；水平扩展需要先把泵的归属与广播移到进程外（第二阶段）。
"""

import asyncio
import logging
import signal
import sys

import uvicorn

from .app import build_runtime, create_internal_app, create_public_app
from .config import Settings, get_settings
from .extensions.expiry import EXIT_LICENSE_ENDED, stop_when_license_ends
from .ops import logredact
from .ops.eventloop import loop_factory

# 编辑器端口上一条 WebSocket 消息最多多大：编辑器经它一次传整块内容（GenOffice 网页模式把二进制转成 base64 放进一帧 JSON，
# 上次保存后新贴的图片跟着保存那一帧走），上限和 GenOffice 自己的一样（ws 库默认 100MiB）。uvicorn 默认的 16MiB 会把
# 贴了大图后的保存断开（1009），保存失败、编辑器把文档当成关掉了（2026-11-17 实测）。
EDITOR_WS_MAX_BYTES = 100 * 1024 * 1024


def _server(app, host: str, port: int, **options) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, lifespan="off", log_config=None, **options))
    server.install_signal_handlers = lambda: None  # 信号由本入口统一处理，两个服务一起退出
    return server


async def serve(settings: Settings, stop: asyncio.Event) -> bool:
    """跑到 stop 置位或某个服务退出为止；返回 True 表示是授权到期停的（调用方据此换退出码）。"""
    runtime = build_runtime(settings)
    public = _server(create_public_app(runtime), settings.public_host, settings.public_port)
    internal = _server(create_internal_app(runtime), settings.internal_host, settings.internal_port)
    # 编辑器端口：配了端口、又有扩展提供编辑器才开（config.editor_port）
    editor = (_server(runtime.editor_app, settings.editor_host, settings.editor_port, ws_max_size=EDITOR_WS_MAX_BYTES)
              if settings.editor_port is not None and runtime.editor_app is not None else None)
    servers = [s for s in (public, internal, editor) if s is not None]
    tasks: list[asyncio.Task] = []
    watcher: asyncio.Task | None = None
    try:
        # 先起内部端口：宿主在第一轮调用模型时就要回调它；运行时就绪后才开放公开端口与编辑器端口
        tasks.append(asyncio.create_task(internal.serve()))
        await runtime.start()
        tasks.append(asyncio.create_task(public.serve()))
        if editor is not None:
            tasks.append(asyncio.create_task(editor.serve()))
        watcher = asyncio.create_task(stop_when_license_ends(runtime.extensions, stop))
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait({*tasks, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.stop()
    return _license_ended(watcher)


def _license_ended(watcher: asyncio.Task | None) -> bool:
    if watcher is None:
        return False
    if not watcher.done():
        watcher.cancel()
        return False
    return not watcher.cancelled() and watcher.exception() is None and watcher.result()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)r}')
    logredact.install()

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        return await serve(get_settings(), stop)

    if asyncio.run(run(), loop_factory=loop_factory()):
        sys.exit(EXIT_LICENSE_ENDED)


if __name__ == "__main__":
    main()
