/**
 * 发给模型的运行时上下文（排在提问之后的那条消息）：去掉工作目录，写本轮的语言规则。
 *
 * 工作目录：官方工作目录服务每轮追加「Current working directory: <宿主机路径>」。宿主没有文件与命令工具，
 * 工作目录对模型无用，还泄露服务器路径；服务本身不能停用（技能目录、子 Agent 都依赖它），只能让这段变空，
 * 空的上下文渲染时被丢掉。
 *
 * 语言：语言由业务服务判定并随消息下发，宿主不猜；规则只写在这一处，
 * 管的是给用户看的文字（调工具前的说明、提问、回答），不管模型思考——思考原文不展示给用户。
 * 放在提问之后而不是系统提示里：Qwen 等模型的对话模板会在系统提示后拼一段英文的工具调用说明，
 * 系统提示里的语言规则在要调工具的回合压不住。子 Agent 跟随根会话的语言。
 *
 * 两处都要做（官方 dsh-system-prompt 的行为）：
 * 1. 每个 agent 作用域里登记上下文：组装后的 refreshContext 会按「作用域优先」重新取文本，
 *    还会补回缺失的必需上下文；停用了可选运行时上下文，所以登记为必需；
 * 2. 在 `system-prompt/assemble` 钩子里把工作目录服务补上的文本清空：它以 prepend 挂在最外层、在 next()
 *    之后写回文本；本插件晚于官方插件加载，同样 prepend 就排在更外层，拿到它写好的结果再清空。
 *
 * 对话界面能显示什么：系统提示里一段固定说明，挂在官方 WEB_SURFACE 的位置（官方用它说明网页界面）。
 * 不写进人设：人设可以被 KNOWWORK_PERSONA 整段替换，这段说明不该跟着丢。
 */

import { resolveRootSession } from './state.mjs'

export const name = 'knowwork-prompt-context'
export const inject = ['agents', 'systemPrompt']

const SILENCED = 'working-directory:current'
const LANGUAGE = 'knowwork:language'
const WEB_SURFACE = 'knowwork:web-surface'

// 前端把 mermaid 代码块画成图（web Markdown.tsx）；要图片文件的仍照常生成文件
const WEB_SURFACE_PROMPT = '对话界面会把回答里的 ```mermaid 代码块直接画成图。用户要流程图、时序图、结构图、'
  + '甘特图这类示意图时，直接在回答里写 mermaid 代码块，不用另外生成图片文件；节点文字里有括号、标点或空格时用双引号括起来。'
  + '用户明确要图片文件，或要把图放进文档、幻灯片时，照常生成文件。'

const LANGUAGE_RULES = {
  zh: '语言要求：所有给用户看的文字都用简体中文，包括调用工具前的说明、重试的原因、向用户的提问和最终回答；'
    + '用户明确要求其他语言时照办。工具参数、代码与文件名保持原样。',
  en: 'Language: write all user-facing text in English, including notes before tool calls, retry explanations, '
    + 'questions to the user and the final answer. Follow an explicit request for another language. '
    + 'Tool arguments, code and file names stay as they are.',
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.systemPrompt.section({
    name: WEB_SURFACE,
    order: ctx.systemPrompt.getSectionOrder('WEB_SURFACE'),
    interpolate: false,
    text: WEB_SURFACE_PROMPT,
  })

  ctx.on('agent/created', ({ agent }) => {
    const prompt = agent.ctx.systemPrompt
    if (prompt === undefined) return
    const order = prompt.getContextOrder('WORKING_DIRECTORY')
    prompt.context({ name: SILENCED, order, required: true, interpolate: false, text: () => '' })
    prompt.context({
      name: LANGUAGE,
      order,
      required: true,
      interpolate: false,
      text: () => LANGUAGE_RULES[resolveRootSession(agent.session)?.language] ?? '',
    })
  })

  ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
    const assembly = await next()
    return {
      ...assembly,
      contexts: assembly.contexts.map(entry => (entry.name === SILENCED ? { ...entry, text: '' } : entry)),
    }
  }, { prepend: true })
}
