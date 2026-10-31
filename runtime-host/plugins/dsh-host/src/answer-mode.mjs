/**
 * 回答方式：智能体（默认）或快速问答。每条消息由业务服务带上，会话中途可以切换。
 *
 * 快速问答只给模型看声明了能在快速问答里用的业务工具（工具描述的 `answerModes`，由业务服务里定义工具的模块自己声明，
 * 如只读知识库工具），照官方 DSH 0.2.2 的做法让「模型看到的」和「这一步能执行的」一致：
 * - 业务工具由工具桥带官方条件（`register(definition, { when })`，见 allowedInMode）注册，每个模型步骤算一次，
 *   快速问答下没声明 quick 的从描述与执行集合里一起去掉；名单不写在宿主里，业务服务加工具不用改宿主；
 * - 官方插件注册的全局工具（技能、待办、交付、提问、子 Agent……）加不了条件，用官方 `tools.restrict({ allow: [] })`
 *   收掉，切回智能体时撤掉（restrict 只管继承来的全局工具，管不到 agent 自己这一层注册的业务工具，两者正好互补）；
 * - 工具变了由官方循环追加一条开发者消息告诉模型（0.2.2 起请求头记有效工具，增删合并成一条）。
 *   原来工具清单不变、靠 `tools.guard` 在执行时拒绝：模型看得到就会去调，被拒后白跑一步（123 上快速问答调
 *   knowledge_documents 被拒，多花一轮）。guard 留作兜底。
 * - 代价：切换回答方式那一步工具声明变了，跟着工具走的官方使用说明（读写文件、子 Agent 等）也一起进出系统提示，
 *   这两样都在请求最前面，提供方前缀缓存那一步整段失效；同一种回答方式里不受影响。
 *   官方计划模式选择不改工具目录，是因为它只禁少数工具；快速问答几乎禁掉全部，取舍相反。
 *
 * 生效点是领取消息时（官方 `agent/inbox/claimed`）：0.2.2 的循环先领取这一步的消息，再组装提示词与工具，
 * 然后才是 `agent/pre-step`，等到 pre-step 再切，这一步的工具已经按旧的回答方式算好了。消息收下时只记下它的
 * 回答方式，领取时才切换：每轮只领一条排队消息，排到下一轮的快速问答消息不会改正在跑的智能体回合。
 *
 * 阅读也放行：只给检索片段时，一条规定、一个列表跨了几个分块，命中开头和中间就只能答一半（智能体模式会接着读，
 * 答得全）；读命中处前后或那一节，不通读整篇。指令写在提问之后（同 prompt-context 的语言规则，原因见那里：
 * Qwen 的对话模板在系统提示后拼英文工具说明，系统提示里的规则在调工具的回合压不住）。
 *
 * 只管宿主打开的根会话：快速问答下子 Agent 工具也被收掉，不会派生子 Agent。
 */

import { getSession } from './state.mjs'

export const name = 'knowwork-answer-mode'
export const inject = ['agents', 'tools', 'systemPrompt']

const CONTEXT = 'knowwork:answer-mode'
/** 工具在哪几种回答方式下提供：业务工具看描述里的声明；没声明的、官方全局工具（传 undefined）只在智能体里 */
const modesOf = tool => tool?.answerModes ?? ['agent']

/**
 * 这个会话此刻能不能用这个工具（工具桥注册业务工具时作为官方条件）。
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

const REJECTED = toolName => `快速问答模式不能调用 ${toolName}；需要时请用户切换到智能体模式`

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  /** 切换回答方式；快速问答时收掉官方全局工具，切回智能体时撤掉 */
  function switchMode(agent, state, mode) {
    state.answerMode = mode
    const masked = state.quickMask !== undefined
    if (mode === 'quick' && !masked) state.quickMask = agent.ctx.tools.restrict({ allow: [] })
    if (mode !== 'quick' && masked) {
      state.quickMask()
      state.quickMask = undefined
    }
  }

  /** 这条消息带来的回答方式：收下时记的，用一次就删 */
  function takeMode(state, message) {
    const mode = state.answerModes.get(String(message.id))
    if (mode !== undefined) state.answerModes.delete(String(message.id))
    return mode
  }

  ctx.on('agent/created', ({ agent }) => {
    const state = getSession(String(agent.session.id))
    if (state === undefined) return
    // 同一会话重开 agent（换运行配置、恢复）时，上一个 agent 的掩码随它释放了；还在快速问答就给新 agent 重新收
    state.quickMask = state.answerMode === 'quick' ? agent.ctx.tools.restrict({ allow: [] }) : undefined
    const prompt = agent.ctx.systemPrompt
    // 停用了可选运行时上下文，登记为必需（同 prompt-context）；智能体模式下文本为空，渲染时被丢掉
    prompt?.context({
      name: CONTEXT,
      order: prompt.getContextOrder('WORKING_DIRECTORY'),
      required: true,
      interpolate: false,
      text: () => (state.answerMode === 'quick' ? QUICK_RULE : ''),
    })
    // 兜底：条件工具已经让模型看不到，凭空调用的也拦下；业务工具按名字找它的描述，找不到的当官方全局工具
    const toolOf = name => state.bridgeTools?.find(tool => tool.name === name)
    agent.ctx.tools.guard(exec => (allowedInMode(state, toolOf(exec.name)) ? undefined : REJECTED(exec.name)))
  })

  ctx.on('agent/inbox/claimed', ({ agent, message }) => {
    const state = getSession(String(agent.session.id))
    if (state === undefined) return
    const mode = takeMode(state, message)
    if (mode !== undefined) switchMode(agent, state, mode)
  })
}
