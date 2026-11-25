# ToolGood.Words.StringSearch.py
# 2020, Lin Zhijun, https://github.com/toolgood/ToolGood.Words
# Licensed under the Apache License 2.0
# 更新日志
# 2020.04.06 第一次提交
# 2020.05.16 修改，支持大于0xffff的字符
"""敏感词的字面匹配：AC 自动机。

建自动机的算法照搬 ToolGood.Words 的 StringSearch（Python 版，随 KnowFlow 带来的那份）：先建字典树，按层
（离根的距离）从浅到深补失败指针，每个结点并上失败指针那边的命中结果；再把每个结点沿失败链能走的转移都并到自己身上，
匹配时一个字只查一次表，找不到就从根重新开始。失败指针按层完整构建，多层重叠的词（「中国」「国人」「中国人」）
都查得到（失败指针只退一层的写法会漏）。

改动：
- 只留建自动机与找全部命中，去掉 FindFirst、ContainsAny、Replace；
- 找命中时一边扫一边去重、限条数（命中多时不先收全再截，免得撑爆内存），另报一共命中了几个不同的词；
- 结点用 __slots__，3 万个词也只占几十兆内存；名字改成 Python 的写法。
"""

from collections.abc import Sequence
from dataclasses import dataclass


class _TrieNode:
    __slots__ = ("char", "children", "failure", "index", "layer", "parent", "results")

    def __init__(self, parent: "_TrieNode | None" = None, char: int = 0) -> None:
        self.index = 0
        self.layer = 0
        self.char = char
        self.results: list[int] = []
        self.children: dict[int, _TrieNode] = {}
        self.failure: _TrieNode | None = None
        self.parent = parent

    def add(self, char: int) -> "_TrieNode":
        node = self.children.get(char)
        if node is None:
            node = _TrieNode(self, char)
            self.children[char] = node
        return node


class _State:
    """压平后的结点：转移已并上失败链，results 是走到这里时命中的词（下标）。"""

    __slots__ = ("moves", "results")

    def __init__(self) -> None:
        self.results: tuple[int, ...] = ()
        self.moves: dict[int, _State] = {}


def _build(words: Sequence[str]) -> _State:
    root = _TrieNode()
    layers: dict[int, list[_TrieNode]] = {}
    for position, word in enumerate(words):
        node = root
        for depth, char in enumerate(word):
            node = node.add(ord(char))
            if node.layer == 0:
                node.layer = depth + 1
                layers.setdefault(node.layer, []).append(node)
        node.results.append(position)

    # 按层从浅到深排：补某个结点的失败指针时，比它浅的都已经补好了
    nodes = [root, *(node for layer in sorted(layers) for node in layers[layer])]
    for index, node in enumerate(nodes):
        if index == 0:
            continue
        node.index = index
        failure = node.parent.failure if node.parent is not None else None
        while failure is not None and node.char not in failure.children:
            failure = failure.failure
        node.failure = root if failure is None else failure.children[node.char]
        for result in node.failure.results:
            if result not in node.results:
                node.results.append(result)
    root.failure = root

    states = [_State() for _ in nodes]
    for node, state in zip(nodes, states, strict=True):
        moves = {char: states[child.index] for char, child in node.children.items()}
        results = list(node.results)
        ancestor = node.failure
        while ancestor is not None and ancestor is not root:
            for char, child in ancestor.children.items():
                moves.setdefault(char, states[child.index])
            results.extend(result for result in ancestor.results if result not in results)
            ancestor = ancestor.failure
        state.moves = moves
        state.results = tuple(results)
    return states[0]


@dataclass(frozen=True)
class Hits:
    words: tuple[str, ...]  # 命中的词，按第一次出现的顺序，最多 limit 个
    total: int  # 一共命中了几个不同的词


class Matcher:
    """一份词表编出来的自动机，编好以后只读，多个请求可以同时用。"""

    def __init__(self, words: Sequence[str]) -> None:
        self._words = tuple(words)
        self._root = _build(self._words)

    def find(self, text: str, *, limit: int) -> Hits:
        root = self._root
        state: _State | None = None
        seen: set[int] = set()
        found: list[str] = []
        for char in text:
            code = ord(char)
            nxt = state.moves.get(code) if state is not None else None
            if nxt is None:
                nxt = root.moves.get(code)
            state = nxt
            if state is None or not state.results:
                continue
            for result in state.results:
                if result in seen:
                    continue
                seen.add(result)
                if len(found) < limit:
                    found.append(self._words[result])
        return Hits(words=tuple(found), total=len(seen))
