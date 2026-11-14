"""按 cases_input.py 的输入跑服务端投影，写出 session_events.json（期望输出）。改了 events_v1.py 后跑一次：

    cd server && uv run python tests/fixtures/events_v1/regen.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cases_input import CASES

from knowwork_server.conversations.events_v1 import ProjectionState, project


def expected(frames: list[dict]) -> list[dict]:
    state, out = ProjectionState(), []
    for frame in frames:
        state, events = project(state, frame["kind"], frame["data"], seq=frame["seq"], time=frame["time"])
        out.extend(events)
    return out


def build() -> list[dict]:
    return [{**case, "expected": expected(case["frames"])} for case in CASES]


if __name__ == "__main__":
    (HERE / "session_events.json").write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n")
    print("wrote", HERE / "session_events.json")
