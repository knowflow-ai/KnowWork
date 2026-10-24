"""逐字输出的状态（conversations/live.py）：开始从头替换，片段按已有字数接着拼，换了一次尝试的片段不认，结束就清；
超过上限后这一步不再推（漏一段再接着拼会拼出缺字的文字）。"""

from knowwork_server.conversations.live import advance, snapshot


def start(attempt="a1", turn=2, step=1):
    return {"type": "start", "attemptId": attempt, "turn": turn, "step": step}


def text(value, attempt="a1"):
    return {"type": "text", "attemptId": attempt, "text": value}


def test_pieces_are_appended_with_their_offset_and_end_clears():
    live, first = advance(None, start())
    assert first == {"run_id": "run-2", "step": 1, "offset": 0, "text": ""}
    live, a = advance(live, text("短剧"))
    live, b = advance(live, text("规模增长"))
    assert (a["offset"], b["offset"], b["text"]) == (0, 2, "规模增长")
    assert snapshot(live) == {"run_id": "run-2", "step": 1, "offset": 0, "text": "短剧规模增长"}
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
