/**
 * 回答方式：智能体（默认）、快速问答，以及业务服务里的扩展登记的其它方式。每条消息由业务服务带上，会话中途可以切换。
 *
 * 一种回答方式管两件事：模型能用哪些工具、提问后面多一段什么指令。
 * - 智能体：不收窄、没有指令；
 * - 快速问答：官方全局工具一个不给，指令是 QUICK_RULE（写在这里，行为同原来）；
 * - 扩展登记的方式：随消息带 answerModeSpec（tools：放行的官方全局工具名；prompt：指令），宿主不保存、不认识它们是什么，
 *   照着收窄与注入。业务服务按会话现算 prompt（可能带上会话相关的摘要），所以每条消息都带。
 * 业务工具看工具描述自己声明的 `answerModes`（由业务服务里定义工具的模块声明），照官方 DSH 0.2.2 的做法让「模型看到的」和
 * 「这一步能执行的」一致：
 * - 业务工具由工具桥带官方条件（`register(definition, { when })`，见 allowedInMode）注册，每个模型步骤算一次，
 *   不在这种回答方式里的从描述与执行集合里一起去掉；名单不写在宿主里，业务服务加工具不用改宿主；
 * - 官方插件注册的全局工具（技能、待办、交付、提问、子 Agent……）加不了条件，用官方 `tools.restrict({ allow })`
 *   收窄，切回智能体时撤掉（restrict 只管继承来的全局工具，管不到 agent 自己这一层注册的业务工具，两者正好互补）；
 * - 工具变了由官方循环追加一条开发者消息告诉模型（0.2.2 起请求头记有效工具，增删合并成一条）。
 *   原来工具清单不变、靠 `tools.guard` 在执行时拒绝：模型看得到就会去调，被拒后白跑一步（123 上快速问答调
 *   knowledge_documents 被拒，多花一轮）。guard 留作兜底。
 * - 代价：切换回答方式那一步工具声明变了，跟着工具走的官方使用说明（读写文件、子 Agent 等）也一起进出系统提示，
 *   这两样都在请求最前面，提供方前缀缓存那一步整段失效；同一种回答方式里不受影响（放行清单不变就不重新收窄）。
 *   官方计划模式选择不改工具目录，是因为它只禁少数工具；快速问答几乎禁掉全部，取舍相反。
 *
 * 生效点是领取消息时（官方 `agent/inbox/claimed`）：0.2.2 的循环先领取这一步的消息，再组装提示词与工具，
 * 然后才是 `agent/pre-step`，等到 pre-step 再切，这一步的工具已经按旧的回答方式算好了。消息收下时只记下它的
 * 回答方式，领取时才切换：每轮只领一条排队消息，排到下一轮的消息不会改正在跑的回合。
 *
 * 快速问答的阅读也放行：只给检索片段时，一条规定、一个列表跨了几个分块，命中开头和中间就只能答一半（智能体模式会接着读，
 * 答得全）；读命中处前后或那一节，不通读整篇。指令写在提问之后（同 prompt-context 的语言规则，原因见那里：
 * Qwen 的对话模板在系统提示后拼英文工具说明，系统提示里的规则在调工具的回合压不住）。
 *
 * 只管宿主打开的根会话：快速问答下子 Agent 工具也被收掉，不会派生子 Agent。
 */

import { getSession } from './state.mjs'

export const name = 'knowwork-answer-mode'
export const inject = ['agents', 'tools', 'systemPrompt']

const CONTEXT = 'knowwork:answer-mode'
/** 宿主自己认得的两种；别的都要随消息带 answerModeSpec */
export const BUILT_IN_MODES = new Set(['agent', 'quick'])
/** 回答方式的标识（同业务服务 conversations/answer_modes.py 与 tool-bridge-client.mjs） */
export const MODE_PATTERN = /^[a-z][a-z0-9_]{0,31}$/
const TOOL_NAME = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/
const MAX_SPEC_TOOLS = 64
const MAX_PROMPT = 20_000
/** 工具在哪几种回答方式下提供：业务工具看描述里的声明；没声明的、官方全局工具（传 undefined）只在智能体里 */
const modesOf = tool => tool?.answerModes ?? ['agent']

/**
 * 这个会话此刻能不能用这个业务工具（工具桥注册业务工具时作为官方条件）。
 * @param {{ answerMode?: string } | undefined} state 根会话状态
 * @param {{ answerModes?: string[] } | undefined} tool 业务工具描述；官方全局工具传 undefined
 */
export const allowedInMode = (state, tool) => modesOf(tool).includes(state?.answerMode ?? 'agent')

const QUICK_RULE = '本轮是快速问答：先调用 knowledge_search 检索，只依据知识库里的内容回答并标注出处，不用先说明要做什么。'
  + '拿不准文档里用什么说法时，把几种说法放在同一步一起检索（会同时执行），不要查一次看一次。'
  + '检索到的片段只是一部分时（一条规定、一个列表或表格只命中了开头或中间），用 knowledge_read 读命中处前后或那一节，读全了再答；'
  + '不要通读整篇。'
  + '检索不到或证据不够就直接说明，不要凭常识补全。'
  + '致谢、寒暄这类不需要检索的话直接回答。'
  + '用户要处理附件、生成文件、执行操作或通读整篇文档时，请他切换到智能体模式。'

const REJECTED = (mode, toolName) => (mode === 'quick'
  ? `快速问答模式不能调用 ${toolName}；需要时请用户切换到智能体模式`
  : `当前回答方式不能调用 ${toolName}；需要时请用户切换到智能体模式`)

/**
 * 校验消息带来的 answerModeSpec（业务服务给的，照样当不可信输入）。
 * @returns {{ tools: string[], prompt: string }}
 */
export function answerModeSpecOf(raw) {
  if (typeof raw !== 'object' || raw === null) throw new Error('非内置的回答方式要带 answerModeSpec（tools、prompt）')
  const { tools, prompt } = raw
  if (!Array.isArray(tools) || tools.length > MAX_SPEC_TOOLS || !tools.every(t => typeof t === 'string' && TOOL_NAME.test(t))) {
    throw new Error('answerModeSpec.tools 要是工具名列表')
  }
  if (typeof prompt !== 'string' || prompt.length > MAX_PROMPT) throw new Error('answerModeSpec.prompt 要是字符串且不超过 20000 字')
  return { tools: [...new Set(tools)].sort(), prompt }
}

/** 官方全局工具放行哪些：智能体不收窄（undefined），快速问答一个不给，扩展登记的按它带来的清单 */
const officialAllowed = state => {
  if (state.answerMode === 'agent') return undefined
  if (state.answerMode === 'quick') return []
  return state.answerModeSpec?.tools ?? []
}

const promptOf = state => {
  if (state.answerMode === 'quick') return QUICK_RULE
  if (state.answerMode === 'agent') return ''
  return state.answerModeSpec?.prompt ?? ''
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  const restrictFor = (agent, state) => {
    const allow = officialAllowed(state)
    return allow === undefined ? undefined : agent.ctx.tools.restrict({ allow })
  }

  /** 切换回答方式；放行清单变了才重新收窄（同一种方式里不动工具声明，不打掉前缀缓存） */
  function switchMode(agent, state, entry) {
    const before = JSON.stringify(officialAllowed(state))
    state.answerMode = entry.mode
    state.answerModeSpec = entry.spec
    if (JSON.stringify(officialAllowed(state)) === before) return
    state.modeMask?.()
    state.modeMask = restrictFor(agent, state)
  }

  /** 这条消息带来的回答方式：收下时记的，用一次就删 */
  function takeMode(state, message) {
    const entry = state.answerModes.get(String(message.id))
    if (entry !== undefined) state.answerModes.delete(String(message.id))
    return entry
  }

  ctx.on('agent/created', ({ agent }) => {
    const state = getSession(String(agent.session.id))
    if (state === undefined) return
    // 同一会话重开 agent（换运行配置、恢复）时，上一个 agent 的掩码随它释放了；还在收窄的方式里就给新 agent 重新收
    state.modeMask = restrictFor(agent, state)
    const prompt = agent.ctx.systemPrompt
    // 停用了可选运行时上下文，登记为必需（同 prompt-context）；智能体模式下文本为空，渲染时被丢掉
    prompt?.context({
      name: CONTEXT,
      order: prompt.getContextOrder('WORKING_DIRECTORY'),
      required: true,
      interpolate: false,
      text: () => promptOf(state),
    })
    // 兜底：条件工具已经让模型看不到，凭空调用的也拦下。业务工具按名字找它的描述；找不到的是官方全局工具，看放行清单
    const toolOf = name => state.bridgeTools?.find(tool => tool.name === name)
    agent.ctx.tools.guard(exec => {
      const tool = toolOf(exec.name)
      const allow = officialAllowed(state)
      const ok = tool !== undefined ? allowedInMode(state, tool) : allow === undefined || allow.includes(exec.name)
      return ok ? undefined : REJECTED(state.answerMode, exec.name)
    })
  })

  ctx.on('agent/inbox/claimed', ({ agent, message }) => {
    const state = getSession(String(agent.session.id))
    if (state === undefined) return
    const entry = takeMode(state, message)
    if (entry !== undefined) switchMode(agent, state, entry)
  })
}
