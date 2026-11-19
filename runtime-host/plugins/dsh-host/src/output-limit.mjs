/**
 * 输出到单次上限之后：模型一次输出达到上限（官方结束原因 max-tokens），官方把写到一半的回答存下、里面的工具调用一个都不执行，
 * 这一轮就此结束。官方循环里 max-tokens 一出现就是这一轮的结局（steer 只能让它多走一步，下一步带了工具调用照样收场），
 * 同一轮里接着做不现实，所以在下一轮告诉模型。
 *
 * 不告诉的后果（2026-11 客户实例）：存下的那条回答是空的（写到一半的工具调用不算内容），下一轮模型看到的上一轮一片空白，
 * 回答用户「没看到上一轮的完整内容」，再照原样把整份脚本一次写出来，差一点又撞上限。
 *
 * 做法照知识库目录（knowledge-catalog.mjs）：每个模型步骤开始前（官方 agent/pre-step）倒着翻会话记录——持久的，宿主重启也在——
 * 找到最近一次 turn/end；它是 max-tokens、而且这次截断还没说过（按回合号认），就把一条 <system-reminder> 追加到这一步的
 * 消息后面（跟在这一轮的提问之后，不动已有前缀），写明截在哪一步、在写什么、要拆小。同一次截断只说一次。
 * 根源上的办法在技能里：生成脚本分文件写（skills/office-* 的「脚本分几个文件写」）。
 */

import { createUserMessage } from '@deepseek-ai/dsh-llm'

export const name = 'knowwork-output-limit'

const KIND = 'knowwork-output-limit'
// 从写到一半的参数里认出在写哪个文件（官方 write、edit 等工具的参数名）
const PATH = /"(?:file_path|path|filePath)"\s*:\s*"([^"\\]{1,300})"/u

/** 一条 assistant/message 的结束原因（官方记在 stream 最后的 finish 片段里）。 */
export function finishKindOf(event) {
  const stream = Array.isArray(event?.data?.stream) ? event.data.stream : []
  for (let index = stream.length - 1; index >= 0; index -= 1) {
    const chunk = stream[index]?.chunk
    if (chunk?.type === 'finish') return chunk.reason?.kind
  }
  return undefined
}

/** 被截断的那次输出：用了多少 token、在调哪个工具、写哪个文件（认不出就不写）。 */
function cutOutput(event) {
  const stream = Array.isArray(event?.data?.stream) ? event.data.stream : []
  const call = stream.findLast(entry => entry?.type === 'tool-call-chunks' && typeof entry.name === 'string')
  const args = Array.isArray(call?.args) ? call.args.join('') : ''
  const outputTokens = event?.data?.usage?.outputTokens
  return {
    ...(Number.isSafeInteger(outputTokens) ? { outputTokens } : {}),
    ...(call ? { tool: call.name } : {}),
    ...(PATH.exec(args) ? { path: PATH.exec(args)[1] } : {}),
  }
}

/**
 * 还没说过的那次截断：最近一次 turn/end 是 max-tokens、而且这一次还没发过说明时，返回 { turn, outputTokens?, tool?, path? }。
 * @param {{ seq: number, eventAt(index: number): any }} session
 */
export function pendingCut(session) {
  const noted = new Set()
  for (let index = session.seq - 1; index >= 0; index -= 1) {
    const event = session.eventAt(index)
    if (event?.type === 'user/message' && event.data?.source?.kind === KIND) {
      noted.add(event.data.source.turn)
      continue
    }
    if (event?.type !== 'turn/end') continue
    const turn = event.data?.turn
    if (event.data?.reason?.kind !== 'max-tokens' || noted.has(turn)) return undefined
    for (let back = index - 1; back >= 0; back -= 1) {
      const earlier = session.eventAt(back)
      if (earlier?.type === 'turn/start') break
      if (earlier?.type === 'assistant/message' && finishKindOf(earlier) === 'max-tokens') return { turn, ...cutOutput(earlier) }
    }
    return { turn }
  }
  return undefined
}

export function noteText({ outputTokens, tool, path }) {
  const size = outputTokens === undefined ? '' : `（输出了 ${outputTokens} 个 token）`
  const doing = tool === undefined
    ? '当时正在写回答，写到一半就停了'
    : `当时正在调用 ${tool}${path === undefined ? '' : `（${path}）`}，这次调用没有执行，内容也没有写进去`
  const advice = tool === undefined
    ? '这一轮回答要分几段写，先给要点。'
    : '这一轮要接着做的话，先把内容拆小：大文件分几次写或拆成几个文件，每次写的内容控制在几千 token 以内。'
  return [
    '<system-reminder>',
    `上一轮你的一次输出达到了单次输出的上限${size}，被截停了：${doing}，那一轮就此结束，用户看到的是「输出超过上限被截断」。`,
    advice,
    '</system-reminder>',
  ].join('\n')
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    const cut = pendingCut(agent.session)
    if (cut === undefined || decision.messages.some(m => m.source?.kind === KIND && m.source.turn === cut.turn)) return decision
    const message = createUserMessage({
      content: [{ type: 'text', text: noteText(cut) }],
      source: { kind: KIND, turn: cut.turn },
    })
    return { ...decision, messages: [...decision.messages, message] }
  })
}
