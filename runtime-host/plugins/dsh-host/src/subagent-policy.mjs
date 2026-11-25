/**
 * 子 Agent 按父会话继承策略。
 *
 * 官方子 Agent（dsh-subagent，origin 为 subagent，会话头记 parentSession）的会话 id 与父会话不同，
 * 宿主为父会话保存的状态查不到。这里按会话头找到经宿主打开的根会话（state.resolveRootSession），并：
 *
 * 1. 工具收窄：把根会话策略里的 deny 按 tool-deny 的规则施加到子 agent。
 *    官方作用域链本身也会把父 agent 的收窄带给子 agent，这里再施加一次，保证不依赖这一实现细节。
 * 2. 审批：官方在委派时把子会话的审批策略钉为 never（子会话里需要审批的操作一律自动拒绝），
 *    理由是子会话没有自己的审批人。KnowWork 有外部审批人（业务服务），审批按根会话归集
 *    （approval-broker），所以对**根会话是宿主会话**的子 Agent，用官方 `setApprovalPolicy`
 *    在子会话日志里追加 ask。官方的委派写入发生在创建窗口内、`agent/created` 之前，后写者生效。
 * 3. 事件：子会话不单独开事件流。它的官方日志事件由 http-host 推进根会话的事件流（`subagent/event`，来源是
 *    host:<子会话>、游标是子会话自己的官方序号）；状态在这里转成进程内的 `subagent/status`。`data.sessionId` 是子会话；
 *    父会话自己日志里的官方 `subagent/catalog` 事件照常出现在根会话流里，二者用 sessionId 对应。
 * 时区照官方：委派给子 Agent 的任务是「父 Agent 发来的消息」，官方时钟只认用户消息带的时区，子 Agent 的读数里
 * 写「时区不可用」（官方说明写明这是设计：回退值不代表用户权威）。不改写官方的消息来源。
 *
 * 技能门不约束子 Agent（skill-gate 只在根会话的回合结束时检查）。
 */

import { setApprovalPolicy } from '@deepseek-ai/dsh-user-approval'
import { forgetLineage, getSession, publish, resolveRootSession } from './state.mjs'
import { applyDeny, denyOf } from './tool-deny.mjs'

export const name = 'knowwork-subagent-policy'
export const inject = ['agents', 'tools']

const isChild = session => getSession(String(session.id)) === undefined && session.header?.origin === 'subagent'

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  const isGlobal = name => ctx.tools.get(name) !== undefined
  ctx.on('agent/created', ({ agent }) => {
    if (!isChild(agent.session)) return
    const root = resolveRootSession(agent.session)
    if (root === undefined) return
    // 被拒的业务工具工具桥本就不注册（父子都不注册），这里只需收窄全局工具并加 guard
    applyDeny(agent, denyOf(root), isGlobal)
    if (ctx.get('approval') !== undefined) setApprovalPolicy(agent.session, 'ask')
  })

  ctx.on('agent/disposed', ({ agent }) => {
    if (isChild(agent.session)) forgetLineage(agent.session.id)
  })

  ctx.on('agent/status', ({ agent, status }) => {
    if (!isChild(agent.session)) return
    const root = resolveRootSession(agent.session)
    if (root !== undefined) publish(root.id, 'subagent/status', { sessionId: String(agent.session.id), status })
  })
}
