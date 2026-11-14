"""服务端投影与共用样例一致（样例也给别处的另一份投影核对，见 fixtures/events_v1/cases_input.py）。

失败说明投影改了而样例没跟上：跑 tests/fixtures/events_v1/regen.py 重新生成，再让另一份投影跟上（它的测试会跟着失败）。"""

import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events_v1"
sys.path.insert(0, str(FIXTURES))

from regen import build


def test_shared_projection_fixtures_match_the_server_projection():
    saved = json.loads((FIXTURES / "session_events.json").read_text())
    assert saved == build()
