"""内核合同在宿主协议上的样子：开会话（SessionSpec）与发一轮（TurnInput）的请求体，宿主插件的收件逻辑（intake.mjs）收这个格式；
宿主级的可选模型清单（HostModel，宿主插件 model-catalog.mjs 收）。

多端同一份合同：云端由 host_client 经 HTTP 发给我们的宿主；在用户设备上执行的对话（conversations/device.py），业务服务把同一份
请求体交给设备，设备原样交给它本机宿主里同一个收件逻辑。两边都只从这里拼，不各写一份，以后合同多一个字段两端一起有。
"""

from .contract import HostModel, SessionSpec, TurnAttachment, TurnInput, TurnMention
from .locale import resolve_turn_language


def session_body(spec: SessionSpec) -> dict:
    """开会话（或以恢复方式重开）的请求体。令牌只在业务服务签了才有：设备上的宿主用设备自己换来的令牌，不在这里。"""
    policy: dict = {"deny": list(spec.policy.deny)} if spec.policy.deny else {}
    if spec.policy.gate is not None:
        gate = {} if spec.policy.gate.required_tool is None else {"requiredTool": spec.policy.gate.required_tool}
        if spec.policy.gate.max_nudges is not None:
            gate = {**gate, "maxNudges": spec.policy.gate.max_nudges}
        policy = {**policy, "gate": gate}
    return {
        "sessionId": spec.session_id, "policy": policy, "resume": spec.resume,
        **({"tools": {"token": spec.tools_token}} if spec.tools_token is not None else {}),
        **({"skills": {"token": spec.skills_token}} if spec.skills_token is not None else {}),
        **({"owner": {"workspaceId": spec.owner.workspace_id, "userId": spec.owner.user_id}}
           if spec.owner is not None else {}),
    }


def turn_body(turn: TurnInput) -> dict:
    """发一轮的请求体：原话、插话与否、语言时区、回答方式、模型、附件、知识库目录、扩展补的上下文、@。"""
    body: dict = {"text": turn.text, "mode": turn.mode, "language": resolve_turn_language(turn.text, turn.language),
                  "answerMode": turn.answer_mode}
    if turn.answer_mode_spec is not None:
        body["answerModeSpec"] = {"tools": list(turn.answer_mode_spec.tools), "prompt": turn.answer_mode_spec.prompt}
    if turn.time_zone is not None:
        body["timeZone"] = turn.time_zone
    if turn.model is not None:
        body["model"] = turn.model
    if turn.attachments:
        body["attachments"] = [_attachment(a) for a in turn.attachments]
    if turn.knowledge is not None:
        body["knowledge"] = {"bases": [{"id": e.id, "name": e.name, "description": e.description,
                                        **({"fields": list(e.fields)} if e.fields else {}),
                                        **({"routes": list(e.routes)} if e.routes else {})}
                                       for e in turn.knowledge.bases], "omitted": turn.knowledge.omitted}
    if turn.context is not None:
        body["context"] = {"standing": turn.context.standing, "turn": turn.context.turn,
                           "persona": turn.context.persona}
    if turn.mentions:
        body["mentions"] = host_mentions(turn.mentions)
    return body


def models_body(models: tuple[HostModel, ...]) -> dict:
    """可选对话模型的清单：宿主据此声明官方模型适配器的路由，没声明的模型选了也发不出去。"""
    return {"models": [{"id": m.id, "name": m.name, "vision": m.vision} for m in models]}


def host_mentions(mentions: tuple[TurnMention, ...] | list[TurnMention]) -> list[dict[str, str]]:
    """@ 交给宿主写进消息时的样子（宿主插件 mentions.mjs 的 mentionsOf 收的格式）。"""
    return [{"type": m.type, "id": m.id, "name": m.name,
             **({"base": m.base, "baseName": m.base_name or m.base} if m.base else {}),
             **({"kind": m.kind, "label": m.label or m.kind} if m.kind else {})}
            for m in mentions]


def _attachment(item: TurnAttachment) -> dict:
    body = {"name": item.name, "path": item.path, "bytes": item.bytes, "sha256": item.sha256}
    if item.extracted_path is not None:
        body = {**body, "extractedPath": item.extracted_path,
                **({"extractionPending": True} if item.extraction_pending else {})}
    if item.digest_path is not None:
        body = {**body, "digestPath": item.digest_path, **({"digestPending": True} if item.digest_pending else {})}
    if item.text_chars is None:
        return body
    reading = {"textChars": item.text_chars, **({"text": item.text} if item.text is not None else {}),
               **({"textPages": item.text_pages} if item.text_pages else {}),
               **({"outline": [{"line": e.line, "text": e.text} for e in item.outline]} if item.outline else {})}
    return {**body, **reading}
