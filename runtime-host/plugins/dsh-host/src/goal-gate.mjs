/**
 * 长任务目标自动接着做下一轮之前，先请业务服务核对。
 *
 * 自动续跑用官方 dsh-goal-round-driver，原样挂（环境层配置里）：目标还没完成、智能体空下来，它就以来源 `goal` 发起下一轮。
 * 官方默认电脑归一个人，自己发起的每一轮没人核对；服务端多人共用，每一轮都要和人发的一样过业务服务这一关：这个人还在
 * 空间里、对话还在且归他、额度够（用定时任务到点时同一个激活接口，kind 是 goal），顺带续好令牌、开着事件泵，回答照常
 * 记进对话。不改官方插件，只在官方「每一步开始前」（agent/pre-step）接一道：
 * - 这一步认领的消息里有来源 `goal` 的（自动续跑发起的那一轮）才问；人发的、别的插件发的不管；
 * - 业务服务不同意（或连不上）：先用官方接口把目标标成卡住、写明原因（界面和模型都看得到），再拒绝这一步。官方续跑插件
 *   看到这一步被拒、目标已经不是进行中，就停下，不会反复重试（它自己的处理，见官方 dsh-goal-round-driver 的 pre-step）；
 * - 要排在官方续跑插件后面挂（它先认这一轮是不是它发的，再交给下游）。
 * 只给服务端宿主：桌面端的本机宿主在用户自己电脑上，官方组合里本来就自动续跑，不经这里。
 */

import { ActivationRefused, createActivationClient } from './multi-user/activation.mjs'
import { getSession } from './state.mjs'

export const name = 'knowwork-goal-gate'
export const inject = ['goals']

/** 业务服务的回应码 → 目标卡住时写给人和模型看的原因 */
const REASONS = {
  403: '你已不是这个空间的成员，长任务目标不能再自动接着做',
  404: '这段对话已不存在或不归你，长任务目标不能再自动接着做',
  503: '对话暂时打不开，长任务目标先停下；稍后用 /goal resume 继续',
}
const UNREACHABLE = '没能确认能不能接着做（业务服务连不上），长任务目标先停下；稍后用 /goal resume 继续'

function reasonOf(error) {
  if (!(error instanceof ActivationRefused)) return UNREACHABLE
  // 额度用完时用额度那边写的话（说清楚为什么、哪天恢复）
  if (error.status === 429) return `额度用完了，长任务目标先停下：${error.reason}`
  return REASONS[error.status] ?? UNREACHABLE
}

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ client?: { activate(request: object): Promise<void> } }} [config] */
export function apply(ctx, config) {
  const client = config?.client ?? createActivationClient()

  ctx.on('agent/pre-step', async (payload, next) => {
    const round = payload.messages.find(message => message.source?.kind === 'goal')
    if (round === undefined) return next()
    const { agent } = payload
    const sessionId = String(agent.session.id)
    const owner = getSession(sessionId)?.owner
    let reason
    if (owner === undefined) {
      reason = '这段对话的属主不明，长任务目标不能自动接着做'
    } else {
      try {
        await client.activate({ kind: 'goal', sessionId, owner })
      } catch (error) {
        reason = reasonOf(error)
        ctx.logger.info('goal round in %s not allowed: %s', sessionId, error instanceof Error ? error.message : String(error))
      }
    }
    if (reason === undefined) return next()
    const goal = ctx.goals.get(agent)
    if (goal !== undefined && goal.phase === 'active') {
      ctx.goals.block(agent, { id: goal.id, revision: goal.revision }, { code: 'not-allowed', message: reason })
    }
    return { kind: 'reject' }
  })
}
