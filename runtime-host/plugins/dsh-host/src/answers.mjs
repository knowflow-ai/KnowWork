/**
 * 收用户对审批与提问的决定（和传输无关，同 intake.mjs）：服务端宿主经 HTTP 收（http-host，业务服务代用户提交），用户电脑上的
 * 本机宿主经主进程的私有通道收（桌面端的收件入口）。两边是同一份逻辑，应答者也是同一对（approval-broker、question-broker，
 * 挂在通用层）：待答的审批与前台等待中的提问记在会话状态里（state.approvals、state.questions），这里按官方编号交回决定。
 *
 * 出错抛 HostError（状态码与错误码，HTTP 那边原样回，主进程那边转成给人看的话）。
 */

import { HostError } from './intake.mjs'
import { AnswerError, checkAnswer, parseAnswer } from './question-answers.mjs'
import { getSession } from './state.mjs'

/** 批准时待答项还没登记，最多等这么久（见 decide） */
const APPROVAL_SETTLE_MS = 1000

/** 回答校验不通过统一成 400 invalid_answer，原因写进 message。 */
function validated(check) {
  try {
    return check()
  } catch (error) {
    if (error instanceof AnswerError) throw new HostError(400, 'invalid_answer', error.message)
    throw error
  }
}

function stateOf(sessionId) {
  const state = getSession(sessionId)
  if (state === undefined) throw new HostError(404, 'session_not_found', `会话不存在：${sessionId}`)
  return state
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ agentOf: (sessionId: string) => object }} env agentOf：这段会话活着的 Agent，不在就抛 HostError 404
 *   （服务端宿主还要排除正在释放的会话，各环境自己判）
 */
export function createAnswers(ctx, { agentOf }) {
  function listApprovals(sessionId) {
    return [...stateOf(sessionId).approvals.values()]
      .map(({ id, sessionId: from, toolName, callId, reason }) => ({ id, sessionId: from, toolName, callId, reason }))
  }

  /**
   * 页面上的卡片取自官方日志（approval/asked），应答者登记待答项比它晚一点（命令跳出沙箱这类要先查一次授权），
   * 刚出卡片就点的话等它一会儿再判「不存在」。outcome 只有官方的两种：allowed-once、rejected。
   */
  async function decide(sessionId, approvalId, outcome) {
    let pending = getSession(sessionId)?.approvals.get(approvalId)
    for (let waited = 0; pending === undefined && getSession(sessionId) !== undefined && waited < APPROVAL_SETTLE_MS; waited += 25) {
      await new Promise(resolve => setTimeout(resolve, 25))
      pending = getSession(sessionId)?.approvals.get(approvalId)
    }
    if (pending === undefined) throw new HostError(404, 'approval_not_found', `审批不存在或已结束：${approvalId}`)
    if (outcome !== 'allowed-once' && outcome !== 'rejected') {
      throw new HostError(400, 'invalid_request', 'outcome 只能是 allowed-once 或 rejected')
    }
    pending.resolve(outcome)
    return { approvalId, outcome }
  }

  /** 官方 userQuestions 投影里本会话还能回答、但已不在前台等待的问题（等待到期、或宿主重启前没答完）。 */
  function continuedQuestions(agent) {
    const view = ctx.get('sessionProjections')?.stateOf(agent.session, 'userQuestions')
    return (view?.questions.active ?? []).filter(question => question.state === 'continued')
  }

  function liveAgent(sessionId) {
    try {
      return agentOf(sessionId)
    } catch {
      return undefined
    }
  }

  /** 待答问题：前台等待中的（open）在前，已转入 continued 的在后。会话没在运行时只看得到前者。 */
  function listQuestions(sessionId) {
    const state = stateOf(sessionId)
    const open = [...state.questions.values()].map(({ id, sessionId: from, questions, timed }) => ({ id, sessionId: from, questions, timed, state: 'open' }))
    const agent = liveAgent(sessionId)
    const continued = agent === undefined ? [] : continuedQuestions(agent)
      .filter(question => !state.questions.has(String(question.callId)))
      .map(question => ({ id: String(question.callId), sessionId, questions: question.questions, timed: true, state: 'continued' }))
    return [...open, ...continued]
  }

  /**
   * 提交回答。前台等待中的问题直接交回；已转入 continued 的经官方 `userQuestions.answer` 以
   * user-question-reply 消息送回 agent（空闲时会开新一轮）。
   */
  function answerQuestion(sessionId, questionId, body) {
    const answer = validated(() => parseAnswer(body))
    const pending = getSession(sessionId)?.questions.get(questionId)
    if (pending !== undefined) {
      validated(() => checkAnswer(pending.questions, answer))
      pending.answer(answer)
      return { questionId, state: 'answered' }
    }
    const agent = agentOf(sessionId)
    const service = ctx.get('userQuestions')
    const question = continuedQuestions(agent).find(item => String(item.callId) === questionId)
    if (service === undefined || question === undefined) {
      throw new HostError(404, 'question_not_found', `问题不存在或已结束：${questionId}`)
    }
    validated(() => checkAnswer(question.questions, answer))
    let accepted
    try {
      accepted = service.answer(agent, question.callId, answer)
    } catch (error) {
      if (error?.code === 'REPLY_QUEUED') throw new HostError(409, 'reply_queued', '这个问题的回答已在排队，等 Agent 接收')
      if (error?.code === 'BAD_ANSWER') throw new HostError(400, 'invalid_answer', '每个问题都要有一条回答')
      throw error
    }
    if (!accepted) throw new HostError(404, 'question_not_found', `问题不存在或已结束：${questionId}`)
    return { questionId, state: 'queued' }
  }

  return { listApprovals, decide, listQuestions, answerQuestion }
}
