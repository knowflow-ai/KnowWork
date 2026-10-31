"""运行详情里「随提问带上的背景」：扩展每轮补的上下文（宿主 turn-context 插件追加的提醒）。常驻那段只在变了时才发，
要往前找到这一轮生效的那一份并注明哪一轮发的；本轮那段只看这一轮追加的；子 Agent 的不算。"""

from knowwork_server.debugview.background import Background, background
from knowwork_server.debugview.events import slice_turns

from .logs import child, frame, text


def reminder(seq, body, *, standing, digest="d1"):
    source = {"kind": "knowwork-turn-context", "digest": digest, **({"standing": True} if standing else {})}
    message = {"role": "user", "content": [{"type": "text", "text": f"<system-reminder>\n{body}\n</system-reminder>"}],
               "source": source}
    return frame(seq, "user/message", message)


LOG = (
    frame(0, "turn/start", {"turn": 1}),
    frame(1, "user/message", text("user", "问一", "user")),
    reminder(2, "召回 X", standing=False),
    reminder(3, "常驻 A", standing=True),
    child(4, "user/message", {**text("user", "子任务的", "knowwork-turn-context")}),
    frame(5, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
    frame(6, "turn/start", {"turn": 2}),
    frame(7, "user/message", text("user", "问二", "user")),
    frame(8, "turn/end", {"turn": 2, "reason": {"kind": "completed"}}),
    frame(9, "turn/start", {"turn": 3}),
    frame(10, "user/message", text("user", "问三", "user")),
    reminder(11, "常驻 B", standing=True, digest="d2"),
    frame(12, "turn/end", {"turn": 3, "reason": {"kind": "completed"}}),
    frame(13, "turn/start", {"turn": 4}),
    reminder(14, "之前随对话提供的背景已经不再适用，不要再按它回答。", standing=True, digest="cleared"),
    frame(15, "turn/end", {"turn": 4, "reason": {"kind": "completed"}}),
)


def test_this_turns_background_and_the_standing_part_in_effect():
    turns = slice_turns(LOG)
    assert background(turns, 1) == (Background("standing", "常驻 A", 1), Background("turn", "召回 X", 1))
    assert background(turns, 2) == (Background("standing", "常驻 A", 1),), "没变就没再发：沿用第 1 轮的"
    assert background(turns, 3) == (Background("standing", "常驻 B", 3),)
    assert background(turns, 4) == (), "清空以后就没有了"
    assert background(turns, 9) == ()
