"""exec 输出的收集与 Docker 多路复用帧格式（解析交给 docker-py 的 frames_iter，这里只留收集与拼帧）。

帧格式：1 字节流号（1 stdout、2 stderr）+ 3 字节 0 + 4 字节大端长度 + 负载。
"""

import struct
from dataclasses import dataclass, field

STDOUT = 1
STDERR = 2


@dataclass
class CappedOutput:
    """按流收集输出，每个流最多 limit 字节，超出部分丢弃并记下截断。"""

    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    def __call__(self, stream: int, payload: bytes) -> None:
        target = self.stderr if stream == STDERR else self.stdout
        room = self.limit - len(target)
        if len(payload) > room:
            self.truncated = True
        if room > 0:
            target.extend(payload[:room])


def frame(stream: int, payload: bytes) -> bytes:
    """按 Docker 的格式拼一帧。测试桩用它扮 daemon，避免另写一份格式。"""
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


MIN_MASKED = 4  # 太短的值（例如 "1"）不替换，否则会把输出里所有同样的字符都换掉
MASK = "******"


def mask(text: str, secrets: list[str]) -> str:
    """把输出里的密钥值（技能的环境变量）换成 ******；长的先换，免得短值是长值的一部分时换不干净。"""
    for secret in sorted((s for s in secrets if len(s) >= MIN_MASKED), key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text
