"""技能工作流的定义：不限步数（同 DSH 官方工作流），只限文件大小。"""

from knowwork_server.skills.workflow_def import parse_workflow


def test_more_than_fifty_steps_is_fine():
    steps = "\n".join(f"  - key: s{i}\n    type: tool\n    tool: kw_now\n    args: {{}}" for i in range(80))
    definition = parse_workflow(f"version: 1\ndescription: 很长的流程\nsteps:\n{steps}\n", None)
    assert len(definition.steps) == 80
