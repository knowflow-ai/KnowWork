"""本机会话与服务端共用的投影样例（输入）：只有 DSH 原生会话事件，不含 KnowWork 插件帧。

不经过业务服务的会话（在用户自己电脑上跑的）由别处另有一份同样的投影，两边都拿 session_events.json 核对：
服务端按这里的输入跑 events_v1.project 生成期望输出（regen.py），test_events_v1_fixtures.py 核对文件与当前投影一致；
另一份投影的测试按同一个文件核对自己的输出。改了投影先跑 regen.py。
"""


def ev(type_, data=None, *, seq, time=1000):
    return {"kind": "session/event", "seq": seq, "time": time,
            "data": {"type": type_, "seq": seq, "time": time, "data": data or {}}}


USER = {"kind": "user"}

CASES = [
    {"name": "一轮正常问答：开始、用户提问、思考与回答、结束", "frames": [
        ev("turn/start", {"turn": 1}, seq=1),
        ev("user/message", {"id": "u1", "source": USER, "content": [{"type": "text", "text": "看看这个文件夹"}]}, seq=2),
        ev("assistant/message", {"message": {"id": "m1", "content": [
            {"type": "thinking", "text": "先列目录"}, {"type": "text", "text": "好的，"},
            {"type": "text", "text": "我来看看。"}]}}, seq=3),
        ev("turn/end", {"turn": 1, "reason": {"kind": "completed"}}, seq=4),
    ]},
    {"name": "工具调用与结果、失败的工具", "frames": [
        ev("turn/start", {"turn": 2}, seq=1),
        ev("tool/call", {"callId": "c1", "name": "bash", "arguments": {"command": "ls"}}, seq=2),
        ev("tool/result", {"message": {"toolCallId": "c1", "content": [{"type": "text", "text": "a.docx\nb.xlsx"}]}}, seq=3),
        ev("tool/call", {"callId": "c2", "name": "read", "arguments": {"path": "x"}}, seq=4),
        ev("tool/result", {"message": {"toolCallId": "c2", "isError": True, "content": [{"type": "text", "text": "不存在"}]},
                           "error": {"code": "ENOENT"}}, seq=5),
        ev("turn/end", {"turn": 2, "reason": {"kind": "max-tokens"}}, seq=6),
    ]},
    {"name": "结束原因：取消、出错、被拦截", "frames": [
        ev("turn/start", {"turn": 3}, seq=1),
        ev("turn/end", {"turn": 3, "reason": {"kind": "aborted"}}, seq=2),
        ev("turn/start", {"turn": 4}, seq=3),
        ev("turn/end", {"turn": 4, "reason": {"kind": "error", "error": {"code": "MODEL_UNAVAILABLE"}}}, seq=4),
        ev("turn/start", {"turn": 5}, seq=5),
        ev("turn/end", {"turn": 5, "reason": {"kind": "blocked"}}, seq=6),
    ]},
    {"name": "宿主注入的消息不展示，定时任务到点展示成触发", "frames": [
        ev("turn/start", {"turn": 6}, seq=1),
        ev("user/message", {"id": "u2", "source": {"kind": "runtime"}, "content": [{"type": "text", "text": "上下文"}]}, seq=2),
        ev("user/message", {"id": "u3", "source": {"kind": "schedule", "title": "日报"},
                            "content": [{"type": "text", "text": "整理今天的进展"}]}, seq=3),
    ]},
    {"name": "待办：每轮一张表，后写的覆盖", "frames": [
        ev("turn/start", {"turn": 7}, seq=1),
        ev("todo/write", {"todos": [{"content": "读文件", "status": "completed"},
                                    {"content": "写总结", "status": "in_progress"},
                                    {"content": "坏的", "status": "unknown"}]}, seq=2),
    ]},
    {"name": "附件说明块换成附件，不进文字", "frames": [
        ev("turn/start", {"turn": 8}, seq=1),
        ev("user/message", {"id": "u4", "source": USER, "content": [
            {"type": "text", "text": "总结一下"},
            {"type": "text", "text": "[File \"报告.docx\" (2048 bytes, sha256:abcdef01): verbatim read-only copy saved at /w/报告.docx]"}]},
            seq=2),
    ]},
    {"name": "不认识的事件不推", "frames": [
        ev("step/start", {"step": 1}, seq=1),
        ev("assistant/chunk", {"text": "半句"}, seq=2),
    ]},
]
