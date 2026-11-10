"""逐字输出的状态（conversations/live.py）：开始从头替换，片段按已有字数接着拼，换了一次尝试的片段不认，结束就清；
超过上限后这一步不再推（漏一段再接着拼会拼出缺字的文字）。"""

from knowwork_server.conversations.live import advance, snapshot


def start(attempt="a1", turn=2, step=1):
    return {"type": "start", "attemptId": attempt, "turn": turn, "step": step}


def text(value, attempt="a1"):
    return {"type": "text", "attemptId": attempt, "text": value}


def test_pieces_are_appended_with_their_offset_and_end_clears():
    live, first = advance(None, start())
    assert first == {"run_id": "run-2", "step": 1, "offset": 0, "text": "", "reasoning": ""}
    live, a = advance(live, text("短剧"))
    live, b = advance(live, text("规模增长"))
    assert (a["offset"], b["offset"], b["text"]) == (0, 2, "规模增长")
    assert snapshot(live) == {"run_id": "run-2", "step": 1, "offset": 0, "text": "短剧规模增长", "reasoning": ""}
    live, done = advance(live, {"type": "end", "attemptId": "a1"})
    assert live is None and done == {"run_id": "run-2", "step": 1, "done": True} and snapshot(live) is None


def test_pieces_from_another_attempt_or_bad_frames_change_nothing():
    live, _ = advance(None, start())
    live, _ = advance(live, text("前"))
    for bad in (text("别的尝试", attempt="a0"), {"type": "end", "attemptId": "a0"}, {"type": "text", "attemptId": "a1"},
                "not a frame", {"type": "start", "attemptId": "a2", "turn": "1", "step": 1}):
        assert advance(live, bad) == (live, None)
    retried, payload = advance(live, start(attempt="a2"))
    assert retried.text == "" and payload["offset"] == 0, "重试的新尝试从头替换"


def test_text_beyond_the_limit_stops_the_step_instead_of_leaving_a_hole(monkeypatch):
    monkeypatch.setattr("knowwork_server.conversations.live.MAX_LIVE_CHARS", 5)
    live, _ = advance(None, start())
    live, _ = advance(live, text("一二三"))
    live, skipped = advance(live, text("四五六"))
    live, after = advance(live, text("七"))
    assert skipped is None and after is None and snapshot(live)["text"] == "一二三"


def test_a_tool_call_being_written_is_announced_once_and_kept_in_the_snapshot():
    live, _ = advance(None, start())
    live, first = advance(live, {"type": "tool", "attemptId": "a1", "name": "write"})
    assert first == {"run_id": "run-2", "step": 1, "tool": "write"}
    assert advance(live, {"type": "tool", "attemptId": "a1", "name": "write"}) == (live, None), "同一个工具只推一次"
    for bad in ({"type": "tool", "attemptId": "a1", "name": "坏 名字"}, {"type": "tool", "attemptId": "a1"},
                {"type": "tool", "attemptId": "a0", "name": "read"}):
        assert advance(live, bad) == (live, None)
    assert snapshot(live) == {"run_id": "run-2", "step": 1, "offset": 0, "text": "", "tools": ["write"], "reasoning": ""}
    retried, _ = advance(live, start(attempt="a2"))
    assert snapshot(retried) == {"run_id": "run-2", "step": 1, "offset": 0, "text": "", "reasoning": ""}, "新一步从头算"


def reasoning(value, attempt="a1"):
    return {"type": "reasoning", "attemptId": attempt, "text": value}


def test_reasoning_is_pushed_with_its_own_offset_and_kept_in_the_snapshot():
    live, _ = advance(None, start())
    live, a = advance(live, reasoning("先看"))
    live, b = advance(live, reasoning("数据"))
    assert (a["reasoning_offset"], b["reasoning_offset"], b["reasoning"]) == (0, 2, "数据")
    assert "offset" not in b and "text" not in b, "思考片段不动正文的字数"
    live, c = advance(live, text("结论"))
    assert c["offset"] == 0 and "reasoning" not in c, "正文从零计数、不带思考（前端据此保留思考）"
    assert snapshot(live) == {"run_id": "run-2", "step": 1, "offset": 0, "text": "结论", "reasoning": "先看数据"}
    assert advance(live, reasoning("")) == (live, None)
    assert advance(live, reasoning("别的尝试", attempt="a0")) == (live, None)


def test_offsets_count_utf16_units_like_the_browser():
    """emoji 在浏览器里占两个码元：offset 按码元算，下一段才接得上（按 Python 字符数会差一位，后面全不拼）。"""
    live, _ = advance(None, start())
    live, _ = advance(live, text("好的📊"))
    live, b = advance(live, text("下一段"))
    assert b["offset"] == 4
    live, _ = advance(live, reasoning("想🤔"))
    live, d = advance(live, reasoning("再想"))
    assert d["reasoning_offset"] == 3
    live, _ = advance(live, text("\ud83d"))  # 宿主把一个 emoji 的两半拆开发
    live, f = advance(live, text("\udcca好"))
    assert f["offset"] == 8
    assert snapshot(live)["text"] == "好的📊下一段\ud83d\udcca好"


def test_reasoning_beyond_the_limit_stops_reasoning_but_not_text(monkeypatch):
    monkeypatch.setattr("knowwork_server.conversations.live.MAX_LIVE_CHARS", 3)
    live, _ = advance(None, start())
    live, _ = advance(live, reasoning("一二"))
    live, skipped = advance(live, reasoning("三四"))
    assert skipped is None and live.reasoning_full and live.reasoning == "一二"
    live, later = advance(live, reasoning("五"))
    assert later is None, "漏了一段就不再接着拼"
    live, body = advance(live, text("正文"))
    assert body is not None and body["text"] == "正文"
