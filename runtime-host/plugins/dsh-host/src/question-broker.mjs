/**
 * 提问应答者：官方 `dsh-tool-ask-user` 经 `dsh-user-questions` 发出 `user-questions/request`，
 * 本插件是最终应答者，等用户在对话里作答（宿主 HTTP 接口代用户提交）。形态照审批应答者：只管答，不另发帧——页面上的
 * 提问卡片由业务服务取自官方日志（ask_user_question 的调用与结果、迟到回答的 user-question-reply 消息，同官方
 * userQuestions 会话投影），用户电脑上的本机会话也是这一条路。
 *
 * 工具用官方 timed 模式（配置在 host-bundle）。实测 legacy 模式的请求不带工具调用 id，问题也不进官方
 * `userQuestions` 投影，宿主一重启问题就丢了；timed 模式两样都有，宿主被杀后恢复会话，官方把中断的调用
 * 记成未知结果，问题转入 continued，仍能回答。
 *
 * 一个问题的结局（都由官方记进会话日志，业务服务据此出卡片的状态）：
 * - 前台等待期内作答 → 回答交回 waterfall，工具结果就是回答；
 * - 前台等待结束（官方期限，或本插件的等待上限，二者取先到）→ timed 调用返回 pending（工具结果带 pending），模型先去
 *   做别的，问题仍可答。之后的回答由 HTTP 驱动经官方 `userQuestions.answer` 以 `user-question-reply` 消息送回，
 *   会话空闲时会开新一轮；
 * - 模型传了 -1（不限时）的调用到上限按超时关闭：官方 `ask()` 只会返回回答或报错，没有 pending 可走；
 * - 回合取消、会话释放 → 中止（工具结果是错误）；
 * - 宿主正常关闭（SIGTERM）→ 同样是中止。这是上游行为：DSH 关闭时整棵插件树并发释放，官方问答服务自己把前台等待以
 *   ASK_ABORTED 中止，工具结果记成「已中止」，恢复会话后问题不能再答（实测，插件之间的释放先后不可控，宿主侧绕不开）。
 *   宿主崩溃（没机会记结果）不受影响：官方恢复时记成未知结果，问题转入 continued。
 *
 * 官方默认的 legacy 模式（用户电脑上的本机宿主用官方智能体预设，预设里的提问工具是它；预设是整份配置，没法只改这一项）
 * 请求不带工具调用 id：照审批应答者「没有 callId 时取最近那条」，从会话日志里找这个会话最近一条题目对得上、还没有结果的
 * ask_user_question 调用，用它的 id。legacy 的问题不进官方投影，宿主重启后就答不了（同上，官方的限制）。
 *
 * 不是经宿主打开的根会话、或认不出是哪次调用（程序化提问）时交给下一个应答者，
 * 没有下一个就按官方的「无人应答」失败关闭。子 Agent 不能提问是官方规则（DELEGATED_CALLER），到不了这里。
 */

import { setImmediate as nextTurn } from 'node:timers/promises'
import { UserQuestionError } from '@deepseek-ai/dsh-user-questions'
import { getSession } from './state.mjs'

export const name = 'knowwork-question-broker'
export const inject = ['userQuestions']

const MAX_TIMEOUT_MS = 24 * 60 * 60 * 1000
const TIMED_OUT = 'ASK_TIMED_OUT'

const ASK_USER = 'ask_user_question'
/** 每个会话最多记这么多条还没结果的提问调用（有结果即删，这只是兜底） */
const MAX_ASKED = 64

const codeOf = reason => typeof reason === 'object' && reason !== null ? reason.code : undefined

/** 一批问题的样子：每题的 id 与题面（请求里的问题由工具参数原样转来，两边对得上） */
export function shapeOf(questions) {
  if (!Array.isArray(questions) || questions.length === 0) return undefined
  return JSON.stringify(questions.map(q => (typeof q === 'object' && q !== null ? [q.id, q.question] : null)))
}

function questionsIn(raw) {
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    return typeof parsed === 'object' && parsed !== null ? parsed.questions : undefined
  } catch {
    return undefined
  }
}

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ timeoutMs?: number }} [config] */
export function apply(ctx, config) {
  const timeoutMs = config?.timeoutMs ?? 30 * 60 * 1000
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error(`knowwork-question-broker: timeoutMs 必须是 1 到 ${MAX_TIMEOUT_MS} 之间的整数毫秒，当前为 ${timeoutMs}`)
  }

  /** 会话 id → 还没结果的 ask_user_question 调用（按出现顺序）：legacy 请求不带 id 时按它认 */
  const asked = new Map()

  ctx.on('session/event', (session, event) => {
    const sessionId = String(session.id)
    const data = event?.data
    if (event?.type === 'tool/call' && data?.name === ASK_USER && typeof data.callId === 'string') {
      const shape = shapeOf(questionsIn(data.arguments))
      if (shape !== undefined) asked.set(sessionId, [...(asked.get(sessionId) ?? []), { callId: data.callId, shape }].slice(-MAX_ASKED))
    } else if (event?.type === 'tool/result' && asked.has(sessionId)) {
      const done = data?.message?.toolCallId
      const rest = asked.get(sessionId).filter(item => item.callId !== done)
      if (rest.length === 0) asked.delete(sessionId)
      else asked.set(sessionId, rest)
    }
  })

  ctx.on('agent/disposed', ({ agent }) => {
    asked.delete(String(agent.session.id))
  })

  /** legacy 请求是哪次调用：这个会话最近一条题目一样、还没结果的 */
  function legacyCall(sessionId, questions) {
    const shape = shapeOf(questions)
    return shape === undefined ? undefined : [...(asked.get(sessionId) ?? [])].reverse().find(item => item.shape === shape)?.callId
  }

  ctx.on('user-questions/request', async (req, next) => {
    const state = req.agent === undefined ? undefined : getSession(String(req.agent.session.id))
    if (state === undefined) return next()
    const sessionId = state.id
    // 官方先记日志再执行工具；日志事件要是晚一拍才到，等一轮事件循环再找
    const callId = req.wait?.callId
      ?? legacyCall(sessionId, req.questions) ?? await nextTurn().then(() => legacyCall(sessionId, req.questions))
    if (callId === undefined) return next()
    if (req.signal?.aborted) throw req.signal.reason
    const id = String(callId)
    const timed = req.wait?.timed === true
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (settle) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        req.signal?.removeEventListener('abort', onAbort)
        if (state.questions.get(id) === entry) state.questions.delete(id)
        settle()
      }
      const close = error => finish(() => reject(error))
      // 前台等待结束：timed 调用会返回 pending（问题仍可答），不限时的调用就此超时关闭——两样都由官方记进日志
      const expire = error => close(error)
      // 中止原因：官方期限到了是 ASK_TIMED_OUT；官方问答服务被释放（宿主正在关闭）是 ASK_ABORTED；回合取消是回合自己的原因
      const onAbort = () => {
        const reason = req.signal.reason
        if (codeOf(reason) === TIMED_OUT) expire(reason)
        else close(reason)
      }
      const timer = setTimeout(() => {
        expire(new UserQuestionError('ask_user_question timed out before the user answered', TIMED_OUT))
      }, timeoutMs)
      const entry = {
        id,
        sessionId,
        questions: req.questions,
        timed,
        answer: answer => finish(() => resolve(answer)),
        cancel: () => close(new UserQuestionError('ask_user_question was aborted before the user answered', 'ASK_ABORTED')),
      }
      req.signal?.addEventListener('abort', onAbort, { once: true })
      state.questions.set(id, entry)
    })
  })
}
