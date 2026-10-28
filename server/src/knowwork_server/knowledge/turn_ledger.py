"""这一轮已经把原文交给模型的片段（按引用编号记）。

同一轮里模型常换着说法再查几次，命中的片段有一部分前面已经整段给过（实测同一会话里约 16% 是重复的）。
第二次只列编号、标明前面给过，模型照样能引用，上下文少读一遍原文。

只按「这一轮」记，不跨轮：前面轮次的原文可能已经被压缩、省略掉，标「给过」会让模型找不到内容。
轮次边界从事件泵的 turn/start、turn/end 得知（挂在泵的观察者上）；进程内记账，多副本时某次调用落到
别的副本，只是这次不去重，照样给原文，不会出错。
"""

from typing import Any


class TurnLedger:
    def __init__(self) -> None:
        self._shown: dict[str, frozenset[int]] = {}  # 会话 → 这一轮给过原文的引用编号；不在里面的会话不记

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        if kind != "session/event" or not isinstance(data, dict):
            return
        if data.get("type") == "turn/start":
            self._shown = {**self._shown, conversation_id: frozenset()}
        elif data.get("type") == "turn/end":
            self._shown = {k: v for k, v in self._shown.items() if k != conversation_id}

    def shown(self, conversation_id: str) -> frozenset[int]:
        return self._shown.get(conversation_id, frozenset())

    def remember(self, conversation_id: str, numbers: frozenset[int]) -> None:
        """记下这次给了原文的编号。没看到这一轮开始的会话不记（宁可不去重，也不跨轮标「给过」）。"""
        if conversation_id in self._shown:
            self._shown = {**self._shown, conversation_id: self._shown[conversation_id] | numbers}
