/**
 * 多用户适配层（五）：属主级插件自己动手时，改道给业务服务。
 *
 * 官方的属主级插件有时自己行动：定时任务到点往会话里放一条消息，以后的事件触发也一样。KnowWork 里每一轮都要业务服务
 * 认可：按这个人当时的权限、用新的令牌、记进对话。官方在这类行动前会发事件要一个活的会话（定时任务是
 * `schedule/resolve-agent`，源码注释写明交给「嵌入方的激活方」，官方 SDK 嵌入时也是这么接的）。这里接住：
 * 1. 请业务服务激活（说清哪类插件、哪段对话、对话归谁——宿主上可能已经没有这段对话，属主从账本里取）：业务服务核对
 *    这个人还在、对话还在且归他，装好策略与令牌、打开宿主会话、开起事件泵；
 * 2. 把宿主上这个活的 Agent 交回给官方，官方照常投递。
 * 业务服务拒绝（人不在了、对话删了）或连不上时抛错，官方按它自己的失败处理走（记日志、下次再试）。
 *
 * 认证用宿主与业务服务之间的共享令牌（业务服务调宿主用的同一个）：两边本来就靠它互信；
 * 要激活哪段对话由宿主说，能不能激活由业务服务按它自己的数据判。
 */

import { getSession } from '../state.mjs'

// 本机开发时业务服务的内部端口；部署编排里配成 api 的内部端口（同其余回调地址）
const DEFAULT_BASE_URL = 'http://127.0.0.1:8001/internal/host/v1'
const TIMEOUT_MS = 30_000

/** @param {{ baseUrl?: string, token?: string }} [options] */
export function createActivationClient(options = {}) {
  const baseUrl = options.baseUrl ?? process.env.KNOWWORK_ACTIVATION_BASE_URL ?? DEFAULT_BASE_URL
  const url = `${baseUrl.replace(/\/+$/, '')}/activations`
  const token = options.token ?? process.env.KNOWWORK_HOST_TOKEN
  return {
    /** @param {{ kind: string, sessionId: string, owner: import('./caller.mjs').Owner }} request */
    async activate(request) {
      if (!token) throw new Error('宿主令牌没有配置，不能请业务服务激活会话')
      const response = await fetch(url, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify(request),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      })
      if (!response.ok) {
        // 业务服务的拒绝原因是一句话；请求体不合规时是逐项的列表
        const detail = (await response.json().catch(() => ({})))?.detail ?? ''
        const reason = typeof detail === 'string' ? detail : JSON.stringify(detail)
        throw new Error(`业务服务没有激活会话 ${request.sessionId}（${response.status}）：${reason}`)
      }
    },
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx 要依赖属主服务 knowworkOwners（owned-service 适配出来的类自带）
 * @param {{ event: string, kind: string, client: ReturnType<typeof createActivationClient> }} options
 *   event：官方要活会话的 waterfall（参数是会话 id）；kind：报给业务服务是哪类插件在激活
 */
export function activateThroughBusiness(ctx, { event, kind, client }) {
  ctx.on(event, async sessionId => {
    const id = String(sessionId)
    const owner = ctx.get('knowworkOwners')?.sessionOwner(id)
    if (owner === undefined) throw new Error(`会话 ${id} 的属主不明，不能请业务服务激活`)
    await client.activate({ kind, sessionId: id, owner })
    const agent = getSession(id)?.agent
    if (agent === undefined || ctx.agents?.get(agent.id) !== agent) {
      throw new Error(`业务服务激活了会话 ${id}，宿主上却没有它的活 Agent`)
    }
    return agent
  })
}
