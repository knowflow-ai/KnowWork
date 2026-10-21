from knowwork_server.sandbox import SandboxSettings, build_sandbox

from .fake_docker import FakeDocker

WS, SID = "ws1", "conv-1"


def settings(**overrides) -> SandboxSettings:
    return SandboxSettings(docker_enabled=True, image="kw-sandbox:1", **overrides)


def make(fake: FakeDocker | None = None, **overrides):
    fake = fake or FakeDocker()
    module = build_sandbox(settings(**overrides), factory=fake.factory)
    assert module is not None
    return fake, module
