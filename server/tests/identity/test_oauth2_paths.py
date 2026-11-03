"""通用 OAuth2 的字段路径：用户信息各家结构不一样，按点分路径取，数字段取数组下标。"""

from knowwork_server.identity.oauth2_client import _text, pick

INFO = {"code": 0, "data": {"users": [{"id": 7, "mail": "a@corp.test"}], "flag": True}}


def test_pick_walks_objects_and_arrays():
    assert pick(INFO, "data.users.0.id") == 7
    assert pick(INFO, "data.users.0.mail") == "a@corp.test"
    assert pick(INFO, "data.users.1.id") is None      # 越界
    assert pick(INFO, "data.users.x") is None         # 数组上用了字段名
    assert pick(INFO, "data.missing.id") is None
    assert pick(INFO, "code.id") is None              # 走到了标量


def test_ids_can_be_numbers_but_not_booleans_or_blank():
    assert _text(7) == "7" and _text(" u1 ") == "u1"
    assert _text(True) is None and _text("  ") is None and _text(None) is None and _text({"id": 1}) is None
