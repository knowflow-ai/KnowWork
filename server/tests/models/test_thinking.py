"""对话模型的「思考」开关：跟随服务默认时请求原样不动；开、关按服务商的写法补参数（按接口地址认服务商）：
自建的 vLLM、SGLang 等用 chat_template_kwargs.enable_thinking，阿里百炼、硅基流动用顶层 enable_thinking，
DeepSeek、智谱、火山方舟、Moonshot 用 thinking.type；认不出写法的（OpenAI）不补。宿主自己带的思考参数以管理员的设置为准。"""

from knowwork_server.models.thinking import with_thinking

BODY = {"messages": [{"role": "user", "content": "hi"}]}
LOCAL = "http://10.0.0.6:8000/v1"


def test_following_the_service_default_leaves_the_request_alone():
    assert with_thinking(BODY, None, LOCAL) is BODY


def test_self_hosted_servers_take_chat_template_kwargs():
    assert with_thinking(BODY, "off", LOCAL) == {**BODY, "chat_template_kwargs": {"enable_thinking": False}}
    sent = {**BODY, "chat_template_kwargs": {"custom": 1, "enable_thinking": True}}
    assert with_thinking(sent, "off", "http://vllm:8000/v1")["chat_template_kwargs"] == {
        "custom": 1, "enable_thinking": False}, "别的模板参数留着"
    assert sent["chat_template_kwargs"]["enable_thinking"] is True, "不改调用方的请求体"


def test_known_services_use_their_own_spelling():
    assert with_thinking(BODY, "off", "https://dashscope.aliyuncs.com/compatible-mode/v1")["enable_thinking"] is False
    assert with_thinking(BODY, "on", "https://api.siliconflow.cn/v1")["enable_thinking"] is True
    assert with_thinking(BODY, "off", "https://api.deepseek.com/v1")["thinking"] == {"type": "disabled"}
    assert with_thinking(BODY, "on", "https://ark.cn-beijing.volces.com/api/v3")["thinking"] == {"type": "enabled"}
    assert with_thinking(BODY, "off", "https://open.bigmodel.cn/api/paas/v4")["thinking"] == {"type": "disabled"}
    assert with_thinking(BODY, "off", "https://api.openai.com/v1") == BODY, "OpenAI 关不掉推理，不乱补"


def test_the_admin_setting_wins_over_what_the_host_sent():
    sent = {**BODY, "enable_thinking": True, "thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    assert with_thinking(sent, "off", LOCAL) == {**BODY, "chat_template_kwargs": {"enable_thinking": False}}
