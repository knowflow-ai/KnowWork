"""请求方的 IP（限次用）。

部署时业务服务在网关后面、端口不对外（docker-compose 里只有网关开端口），连上来的是网关，request.client 是网关的地址；
网关把真正连它的地址写进 X-Real-IP（deploy/gateway/nginx.conf，会覆盖浏览器自己带的同名头，所以伪造不了）。
本机开发经 Vite 代理、没有这个头时退回连接地址。网关前面还有一层代理时拿到的是那层代理的地址：按 IP 的限次会粗一些，
但不会被伪造。
"""

from fastapi import Request


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-real-ip") or "").strip()
    if forwarded:
        return forwarded[:64]
    return request.client.host if request.client else ""
