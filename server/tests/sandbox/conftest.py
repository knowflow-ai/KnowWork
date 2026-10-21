import pytest

from . import helpers


@pytest.fixture
async def make():
    """建沙箱模块（假 docker-py 客户端），测试结束时关掉，免得 exec 读线程池残留。"""
    built = []

    def factory(fake=None, **overrides):
        fake, module = helpers.make(fake, **overrides)
        built.append(module)
        return fake, module

    yield factory
    for module in built:
        await module.aclose()


