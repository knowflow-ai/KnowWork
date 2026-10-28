/**
 * 回答方式：智能体（默认）或快速问答。每条消息由业务服务带上，会话中途可以切换（照 WeKnora 的快速问答与智能推理）。
 *
 * 照官方计划模式（dsh-plan-mode）的形态做中途切换：
 * - 工具清单与系统提示不随切换变化：官方计划模式进出「只改变提示词段落，绝不改变请求的工具目录」，
 *   工具目录一变前缀缓存就失效；这里连系统提示也不动，指令写在提问之后（同 prompt-context 的语言规则，
 *   原因见那里：Qwen 的对话模板在系统提示后拼英文工具说明，系统提示里的规则在调工具的回合压不住）；
 * - 约束主要靠指令，另加一道官方 `tools.guard` 兜底：快速问答只放行知识库检索，别的调用直接拒绝并说明原因；
 * - 生效点是消息进入步骤（官方 `agent/pre-step`，计划模式切换也在这里落地）：消息收下时只记下它的回答方式，
 *   进入步骤才切换。每轮只领一条排队消息，排到下一轮的快速问答消息不会把正在跑的智能体回合的工具拦下。
 *
 * 只管宿主打开的根会话：快速问答下 subagent 也被拦下，不会派生子 Agent。
 */

import { SEARCH_TOOL } from './knowledge-catalog.mjs'
import { getSession } from './state.mjs'

export const name = 'knowwork-answer-mode'
export const inject = ['agents', 'tools', 'systemPrompt']

const CONTEXT = 'knowwork:answer-mode'
const QUICK_TOOLS = new Set([SEARCH_TOOL])

const QUICK_RULE = '本轮是快速问答：先调用 knowledge_search 检索，只依据检索到的内容回答并标注出处，不用先说明要做什么。'
  + '检索不到或证据不够就直接说明，不要凭常识补全。'
  + '致谢、寒暄这类不需要检索的话直接回答。'
  + '用户要处理附件、生成文件、执行操作或通读整篇文档时，请他切换到智能体模式。'

const REJECTED = toolName => `快速问答模式只能检索知识库，不能调用 ${toolName}；需要时请用户切换到智能体模式`

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('agent/created', ({ agent }) => {
    const state = getSession(String(agent.session.id))
    if (state === undefined) return
    const prompt = agent.ctx.systemPrompt
    // 停用了可选运行时上下文，登记为必需（同 prompt-context）；智能体模式下文本为空，渲染时被丢掉
    prompt?.context({
      name: CONTEXT,
      order: prompt.getContextOrder('WORKING_DIRECTORY'),
      required: true,
      interpolate: false,
      text: () => (state.answerMode === 'quick' ? QUICK_RULE : ''),
    })
    agent.ctx.tools.guard(exec => (state.answerMode === 'quick' && !QUICK_TOOLS.has(exec.name) ? REJECTED(exec.name) : undefined))
  })

  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    const state = getSession(String(agent.session.id))
    if (state === undefined) return decision
    for (const message of decision.messages) {
      const mode = state.answerModes.get(String(message.id))
      if (mode === undefined) continue
      state.answerMode = mode
      state.answerModes.delete(String(message.id))
    }
    return decision
  })
}
