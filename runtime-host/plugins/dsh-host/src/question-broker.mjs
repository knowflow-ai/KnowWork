/**
 * 提问应答者：官方 `dsh-tool-ask-user` 经 `dsh-user-questions` 发出 `user-questions/request`，
 * 本插件是最终应答者，把问题转给业务服务，等用户在对话里作答（宿主 HTTP 接口代用户提交）。形态照审批应答者。
 *
 * 工具用官方 timed 模式（配置在 host-bundle）。实测 legacy 模式的请求不带工具调用 id，问题也不进官方
 * `userQuestions` 投影，宿主一重启问题就丢了；timed 模式两样都有，宿主被杀后恢复会话，官方把中断的调用
 * 记成未知结果，问题转入 continued，仍能回答。
 *
 * 一个问题的结局：
 * - 前台等待期内作答 → 回答交回 waterfall，工具结果就是回答（question/answered）；
 * - 前台等待结束（官方期限，或本插件的等待上限，二者取先到）→ timed 调用返回 pending，模型先去做别的，
 *   问题仍可答（question/continued）。之后的回答由 HTTP 驱动经官方 `userQuestions.answer` 以
 *   `user-question-reply` 消息送回，会话空闲时会开新一轮；该消息进入会话日志时这里记一条 question/answered；
 * - 模型传了 -1（不限时）的调用到上限按超时关闭（question/closed，reason=timeout）：官方 `ask()` 只会返回回答
 *   或报错，没有 pending 可走；
 * - 回合取消、会话释放 → question/closed（reason=cancelled）；
 * - 宿主正常关闭（SIGTERM）→ question/closed（reason=interrupted）。这是上游行为：DSH 关闭时整棵插件树并发释放，
 *   官方问答服务自己把前台等待以 ASK_ABORTED 中止，工具结果记成「已中止」，恢复会话后问题不能再答（实测，
 *   插件之间的释放先后不可控，宿主侧绕不开）。界面据此提示用户直接在输入框回复。宿主崩溃（没机会记结果）
 *   不受影响：官方恢复时记成未知结果，问题转入 continued。
 *
 * 不是经宿主打开的根会话、或请求不带工具调用 id（legacy 调用、程序化提问）时交给下一个应答者，
 * 没有下一个就按官方的「无人应答」失败关闭。子 Agent 不能提问是官方规则（DELEGATED_CALLER），到不了这里。
 */

import { UserQuestionError } from '@deepseek-ai/dsh-user-questions'
import { getSession, publish } from './state.mjs'

export const name = 'knowwork-question-broker'
export const inject = ['userQuestions']

const MAX_TIMEOUT_MS = 24 * 60 * 60 * 1000
const TIMED_OUT = 'ASK_TIMED_OUT'

const codeOf = reason => typeof reason === 'object' && reason !== null ? reason.code : undefined

/** 迟到回答消息（官方 user-question-reply）里的回答批次；读不出来时返回 undefined。 */
function lateAnswers(content) {
  const text = Array.isArray(content) ? content.find(block => block?.type === 'text')?.text : undefined
  if (typeof text !== 'string') return undefined
  try {
    const parsed = JSON.parse(text)
    return Array.isArray(parsed?.answers) ? parsed.answers : undefined
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

  ctx.on('user-questions/request', (req, next) => {
    const callId = req.wait?.callId
    const state = req.agent === undefined ? undefined : getSession(String(req.agent.session.id))
    if (state === undefined || callId === undefined) return next()
    if (req.signal?.aborted) return Promise.reject(req.signal.reason)
    const id = String(callId)
    const sessionId = state.id
    const timed = req.wait?.timed === true
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (event, data, settle) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        req.signal?.removeEventListener('abort', onAbort)
        if (state.questions.get(id) === entry) state.questions.delete(id)
        publish(sessionId, event, { id, sessionId, ...data })
        settle()
      }
      const close = (reason, error) => finish('question/closed', { reason }, () => reject(error))
      // 前台等待结束：timed 调用会返回 pending（问题仍可答），不限时的调用就此超时关闭
      const expire = error => timed ? finish('question/continued', {}, () => reject(error)) : close('timeout', error)
      // 中止原因：官方期限到了是 ASK_TIMED_OUT；官方问答服务被释放（宿主正在关闭）是 ASK_ABORTED；回合取消是回合自己的原因
      const onAbort = () => {
        const reason = req.signal.reason
        const code = codeOf(reason)
        if (code === TIMED_OUT) expire(reason)
        else close(code === 'ASK_ABORTED' ? 'interrupted' : 'cancelled', reason)
      }
      const timer = setTimeout(() => {
        expire(new UserQuestionError('ask_user_question timed out before the user answered', TIMED_OUT))
      }, timeoutMs)
      const entry = {
        id,
        sessionId,
        questions: req.questions,
        timed,
        answer: answer => finish('question/answered', { answers: answer.answers, late: false }, () => resolve(answer)),
        cancel: () => close('cancelled', new UserQuestionError('ask_user_question was aborted before the user answered', 'ASK_ABORTED')),
      }
      req.signal?.addEventListener('abort', onAbort, { once: true })
      state.questions.set(id, entry)
      publish(sessionId, 'question/requested', { id, sessionId, questions: req.questions, timed })
    })
  })

  // 迟到回答在会话日志里落成一条 user-question-reply 用户消息，官方投影也以它为准关闭问题
  ctx.on('session/event', (session, event) => {
    if (event.type !== 'user/message' || event.data?.source?.kind !== 'user-question-reply') return
    const state = getSession(String(session.id))
    if (state === undefined) return
    const answers = lateAnswers(event.data.content)
    if (answers === undefined) return
    publish(state.id, 'question/answered', { id: String(event.data.source.callId), sessionId: state.id, answers, late: true })
  })
}
