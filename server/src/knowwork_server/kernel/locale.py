"""本轮交互语言：用户明确指定了语言就用它；
没指定时（工作流等没有聊天界面的调用）提问里有汉字就是中文，否则英文。

浏览器在聊天页的输入框旁边选语言，每条消息都带上。只看提问文字会误判：比如中文对话里只回了一个
仓库路径，没有汉字，就被判成英文。宿主据此在提问之后写一句语言规则，管给用户看的文字
（调工具前的说明、提问、回答）。判定只在业务服务做一次，宿主不猜。
"""

import re
from typing import Literal

Language = Literal["zh", "en"]
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def resolve_turn_language(text: str, explicit: Language | None = None) -> Language:
    if explicit is not None:
        return explicit
    return "zh" if _HAN.search(text) else "en"
