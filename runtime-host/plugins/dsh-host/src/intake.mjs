/**
 * 收合同：业务服务用同一份代码（业务服务的 kernel/wire.py）编出的开会话与每一轮的合同，在宿主里落成会话状态与这一轮的消息。
 *
 * 和传输无关，也和谁管 Agent 的生灭无关，两种宿主共用：
 * - 服务端宿主（http-host）：经 HTTP 收合同，自己建、恢复、释放 Agent（官方最小组合里没有会话控制器）；
 * - 用户设备上的本机宿主：Agent 的建与恢复照 DSH 官方桌面版归官方会话控制器（它给 Agent 装预设与模型选择，它恢复的 Agent
 *   只有它能释放），会话状态在官方 agent/created 时按开会话的合同建好，每一轮经只给主进程的入口交来。
 * 合同多一个字段：业务服务的 wire.py 与这里各改一处，两种宿主一起有；不在某一种宿主里单独收。
 */

import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { attachmentBlocks, attachmentsOf } from './attachment-handles.mjs'
import { catalogOf } from './knowledge-catalog.mjs'
import { mentionBlocks, mentionsOf } from './mentions.mjs'
import { receiveTurnContext, turnContextOf } from './turn-context.mjs'
import { BUILT_IN_MODES, MODE_PATTERN, answerModeSpecOf } from './answer-mode.mjs'
import { deploymentTimeZone, userSource } from './message-source.mjs'
import { MODEL_ID } from './model-catalog.mjs'
import { createSession } from './state.mjs'

/** 宿主拒收的请求：status 照 HTTP 状态码的含义，code 是给调用方认的错误码，message 给人看（业务服务只记日志）。 */
export class HostError extends Error {
  constructor(status, code, message) {
    super(message)
    this.status = status
    this.code = code
  }
}

const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/

export function requireString(value, field) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new HostError(400, 'invalid_request', `缺少字段 ${field}`)
  }
  return value
}

export function checkSessionId(id) {
  if (!SESSION_ID_PATTERN.test(id)) throw new HostError(400, 'invalid_request', 'sessionId 只允许字母、数字和 _.:-，最长 128')
  return id
}

function objectOf(raw, field) {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new HostError(400, 'invalid_request', `${field} 必须是对象`)
  return raw
}

function policyOf(raw) {
  if (raw === undefined) return {}
  objectOf(raw, 'policy')
  const deny = raw.deny
  if (deny !== undefined && (!Array.isArray(deny) || deny.some(n => typeof n !== 'string'))) {
    throw new HostError(400, 'invalid_request', 'policy.deny 必须是字符串数组')
  }
  if (raw.gate === undefined) return deny === undefined ? {} : { deny: [...deny] }
  const { requiredTool, maxNudges } = raw.gate ?? {}
  if (requiredTool !== undefined) requireString(requiredTool, 'policy.gate.requiredTool')
  if (maxNudges !== undefined && !(Number.isInteger(maxNudges) && maxNudges >= 0 && maxNudges <= 5)) {
    throw new HostError(400, 'invalid_request', 'policy.gate.maxNudges 必须是 0 到 5 的整数')
  }
  const gate = { ...(requiredTool === undefined ? {} : { requiredTool }), ...(maxNudges === undefined ? {} : { maxNudges }) }
  return { ...(deny === undefined ? {} : { deny: [...deny] }), gate }
}

/** 回调业务服务的短期令牌（工具桥受众 tools、技能提供方受众 skills）。宿主只转交，不解读。 */
export function toolTokenOf(value, field) {
  requireString(value, field)
  if (value.length < 32 || value.length > 4096) throw new HostError(400, 'invalid_request', `${field} 长度不合法`)
  return value
}

function accessOf(raw, field) {
  if (raw === undefined) return undefined
  return { token: toolTokenOf(objectOf(raw, field).token, `${field}.token`) }
}

/** 这段对话的属主（空间、人），业务服务给的；宿主只拿它记录、比对属主级资源的归属，不据此判权限。 */
function ownerOf(raw) {
  if (raw === undefined) return undefined
  objectOf(raw, 'owner')
  const pick = field => {
    const value = raw[field]
    if (typeof value !== 'string' || value.length === 0 || value.length > 128) {
      throw new HostError(400, 'invalid_request', `owner.${field} 必须是 1 到 128 个字符的字符串`)
    }
    return value
  }
  return { workspaceId: pick('workspaceId'), userId: pick('userId') }
}

/**
 * 开会话的合同 → 校验过的样子。恢复会话必须重新给出策略：宿主不保存策略，每次打开都以业务服务此刻的为准。
 * @returns {{ sessionId: string, resume: boolean, policy: object, toolAccess?: { token: string },
 *   skillAccess?: { token: string }, owner?: { workspaceId: string, userId: string } }}
 */
export function sessionContractOf(body) {
  objectOf(body, '开会话的合同')
  const sessionId = checkSessionId(requireString(body.sessionId, 'sessionId'))
  const resume = body.resume === true
  if (resume && body.policy === undefined) {
    throw new HostError(400, 'policy_required', '恢复会话必须重新给出 policy，宿主不保存策略')
  }
  return {
    sessionId,
    resume,
    policy: policyOf(body.policy),
    toolAccess: accessOf(body.tools, 'tools'),
    skillAccess: accessOf(body.skills, 'skills'),
    owner: ownerOf(body.owner),
  }
}

/**
 * 按合同建这段对话的会话状态。要在 Agent 建出来之前、或在它的 agent/created 最前面：会话策略、技能、工具桥等插件
 * 都在 agent/created 里读它。
 */
export function provision(contract) {
  const state = createSession(contract.sessionId, contract.policy)
  state.toolAccess = contract.toolAccess
  state.skillAccess = contract.skillAccess
  state.owner = contract.owner
  return state
}

/**
 * 收一轮：把这一轮的合同写成用户消息交给 Agent，本轮的语言、时区、知识库目录、回答方式、扩展补的上下文记进会话状态，
 * 由各自的插件（prompt-context、knowledge-catalog、answer-mode、turn-context）在这条消息进入步骤时拆包。
 * @param {{ models: { knows: (id: string) => boolean }, provider: string, fallbackTimeZone?: string }} options
 *   models：宿主现在能用的模型（model-catalog）；provider：选模型时的模型来源；fallbackTimeZone：消息没带时区时用，默认宿主所在的时区
 */
export function createTurnIntake({ models, provider, fallbackTimeZone = deploymentTimeZone() }) {
  /**
   * 用户在输入框选了模型：和这段对话当前的不一样就按官方的会话级选择换（存进会话，从下一步生效，重启恢复后照旧）。
   * 宿主没收到过的模型直接拒（409 model_unknown），业务服务重推清单后再发，不让这一步跑到模型那里才以 UNKNOWN_MODEL 失败。
   */
  function selectModel(agent, model) {
    if (model === undefined || model === null) return
    if (typeof model !== 'string' || !MODEL_ID.test(model)) throw new HostError(400, 'invalid_request', 'model 格式不对')
    if (!models.knows(model)) throw new HostError(409, 'model_unknown', `宿主还不认识模型：${model}`)
    const current = agent.model.current()
    if (current?.provider === provider && current.model === model) return
    agent.model.select({ provider, model })
  }

  function partsOf(body) {
    try {
      return {
        files: attachmentsOf(body.attachments),
        knowledge: catalogOf(body.knowledge),
        mentions: mentionsOf(body.mentions),
        context: turnContextOf(body.context),
      }
    } catch (error) {
      throw new HostError(400, 'invalid_request', error.message)
    }
  }

  /**
   * @param {object} agent 活的 Agent
   * @param {import('./state.mjs').SessionState | undefined} state 这段对话的会话状态
   * @param {object} body 这一轮的合同
   * @returns {{ messageId: string, mode: 'followup' | 'steer' }}
   */
  return function receiveTurn(agent, state, body) {
    objectOf(body, '这一轮的合同')
    const text = requireString(body.text, 'text')
    const mode = body.mode ?? 'followup'
    if (mode !== 'followup' && mode !== 'steer') throw new HostError(400, 'invalid_request', 'mode 只能是 followup 或 steer')
    const language = body.language
    if (language !== undefined && language !== 'zh' && language !== 'en') {
      throw new HostError(400, 'invalid_request', 'language 只能是 zh 或 en')
    }
    const answerMode = body.answerMode ?? 'agent'
    if (typeof answerMode !== 'string' || !MODE_PATTERN.test(answerMode)) {
      throw new HostError(400, 'invalid_request', 'answerMode 格式不对')
    }
    // 扩展登记的回答方式随消息带上放行的官方工具与指令（answer-mode）；内置的两种宿主自己认得
    let answerModeSpec
    if (!BUILT_IN_MODES.has(answerMode)) {
      try {
        answerModeSpec = answerModeSpecOf(body.answerModeSpec)
      } catch (error) {
        throw new HostError(400, 'invalid_request', error.message)
      }
    }
    const { files, knowledge, mentions, context } = partsOf(body)
    selectModel(agent, body.model)
    if (state !== undefined && language !== undefined) state.language = language
    if (state !== undefined && knowledge !== undefined) state.knowledgeCatalog = knowledge
    const content = [{ type: 'text', text }, ...attachmentBlocks(files), ...mentionBlocks(mentions)]
    const source = userSource(body.timeZone, fallbackTimeZone)
    if (state !== undefined) state.timeZone = source.clientTimeZone
    const message = createUserMessage({ content, source })
    // 回答方式等这条消息进入步骤时才生效（answer-mode）：排队到下一轮的消息不改正在跑的这一轮
    state?.answerModes.set(String(message.id), { mode: answerMode, spec: answerModeSpec })
    receiveTurnContext(state, message.id, context)
    if (mode === 'steer') agent.steer(message)
    else agent.followup(message)
    return { messageId: message.id, mode }
  }
}
