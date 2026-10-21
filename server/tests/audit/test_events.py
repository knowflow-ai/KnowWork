"""审计事件的校验与 detail 脱敏：误传的提示词、密钥、正文都不能原样落库。"""

import pytest

from knowwork_server.audit.events import MAX_DETAIL_KEYS, MAX_VALUE_LENGTH, REDACTED, AuditEvent, sanitize_detail


def event(**overrides) -> AuditEvent:
    return AuditEvent(**{"workspace_id": "ws1", "actor_type": "human", "action": "member.add", "result": "success",
                         **overrides})


def test_sensitive_keys_are_dropped():
    cleaned = sanitize_detail({"password": "x", "apiKey": "ak_x", "api_key": "y", "accessToken": "t", "prompt": "p",
                               "answerText": "a", "content": "c", "messageText": "m", "arguments": "{}",
                               "query": "合同", "role": "admin", "toolName": "kw_note", "context": "ok"})
    assert cleaned == {"role": "admin", "toolName": "kw_note", "context": "ok"}


def test_secret_looking_values_are_redacted():
    cleaned = sanitize_detail({"note": "用 Bearer abc.def 调用，key ak_0123456789abcdef，另有 sk-ABCDEFGHIJ",
                               "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"})
    assert "abc.def" not in cleaned["note"] and "ak_0123" not in cleaned["note"] and "sk-ABC" not in cleaned["note"]
    assert cleaned["note"].count(REDACTED) == 3
    assert cleaned["jwt"] == REDACTED


def test_values_are_bounded_and_nested_structures_skipped():
    detail = {f"k{i}": i for i in range(MAX_DETAIL_KEYS + 5)}
    assert len(sanitize_detail(detail)) == MAX_DETAIL_KEYS
    cleaned = sanitize_detail({"long": "x" * 1000, "nested": {"a": 1}, "items": ["a", {"b": 2}, 3], "flag": True,
                               "none": None, "ratio": 0.5})
    assert len(cleaned["long"]) == MAX_VALUE_LENGTH
    assert "nested" not in cleaned
    assert cleaned["items"] == ["a", 3]
    assert (cleaned["flag"], cleaned["none"], cleaned["ratio"]) == (True, None, 0.5)
    assert sanitize_detail(None) == {} and sanitize_detail({}) == {}


@pytest.mark.parametrize("field,value", [("workspace_id", ""), ("actor_type", "robot"), ("result", "ok"),
                                         ("action", "")])
def test_invalid_events_are_rejected(field, value):
    with pytest.raises(ValueError):
        event(**{field: value})


def test_normalized_clips_and_returns_a_new_event():
    original = event(resource_id="r" * 500, ip="1" * 100, detail={"secret": "s", "role": "viewer"})
    normalized = original.normalized()
    assert normalized is not original and original.resource_id == "r" * 500
    assert len(normalized.resource_id) == 200 and len(normalized.ip) == 64
    assert normalized.detail == {"role": "viewer"}
    row = original.row()
    assert row["detail"] == {"role": "viewer"} and row["id"].startswith("al_") and row["occurred_at"].tzinfo
