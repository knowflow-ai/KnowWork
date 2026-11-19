/**
 * 一轮结束前核对待办：待办是模型用官方 todo_write 维护的，官方只在工具说明里要求「做完一项就标完成」，
 * 模型常在交付文件、写完回答之后漏掉最后一次更新，界面就停在「已完成 3/4」，看上去像没做完（2026-11 客户实例：
 * 第 8 步把「交付文件」标成进行中，第 9 步交付、第 10 步写回答，再没更新）。
 *
 * 用官方 agent/turn-stopping（同技能门 skill-gate.mjs）：这一轮正常要结束、本轮最后写的那份待办还有没完成的项，
 * 就 agent.steer() 一条 <system-reminder> 让模型再走一步，把待办更新掉。每轮最多提醒一次；
 * 被截断的一轮（最后一步的结束原因是 max-tokens）不提醒——本来就没做完，界面会说明截断。
 * 判断全靠倒着翻本轮的会话记录（持久的），宿主重启后照样认得出提醒过没有。
 * 提醒之后那次 todo_write 和收尾的一句话不该顶掉原来的回答：业务服务的事件投影把它们藏起来（conversations/events_v1.py）。
 */

import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { finishKindOf } from './output-limit.mjs'

export const name = 'knowwork-todo-check'

const KIND = 'knowwork-todo-check'
const STATUS = Object.freeze({ pending: '未开始', in_progress: '进行中' })

/**
 * 本轮的情况：最后写的那份待办、最后一步是不是被截断、提醒过没有。
 * @param {{ seq: number, eventAt(index: number): any }} session
 */
export function turnState(session) {
  let todos
  let finish
  let nudged = false
  for (let index = session.seq - 1; index >= 0; index -= 1) {
    const event = session.eventAt(index)
    if (event?.type === 'turn/start') break
    if (event?.type === 'todo/write' && todos === undefined && Array.isArray(event.data?.todos)) todos = event.data.todos
    if (event?.type === 'assistant/message' && finish === undefined) finish = finishKindOf(event) ?? null
    if (event?.type === 'user/message' && event.data?.source?.kind === KIND) nudged = true
  }
  return { todos, truncated: finish === 'max-tokens', nudged }
}

export function reminderText(open) {
  return [
    '<system-reminder>',
    '这一轮结束前，待办里还有没标完成的：',
    ...open.map(todo => `- ${todo.content}（${STATUS[todo.status] ?? todo.status}）`),
    // 不能叫它什么都别说：最后一步一个字不输出，官方当成空回复报错（EMPTY_RESPONSE），这一轮反倒以出错收场。
    // 这句收尾话和那次 todo_write 由业务服务的事件投影藏起来（conversations/events_v1.py），用户看到的还是原来的回答
    '做完了的用 todo_write 标成 completed；不做了的也标成 completed 或从清单里去掉；要等用户回答才能接着做的保持原样。更新完用一句话收尾，不要重复前面的回答。',
    '</system-reminder>',
  ].join('\n')
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    const { todos, truncated, nudged } = turnState(agent.session)
    if (todos === undefined || truncated || nudged) return
    const open = todos.filter(todo => todo?.status !== 'completed' && typeof todo?.content === 'string')
    if (open.length === 0) return
    agent.steer(createUserMessage({
      content: [{ type: 'text', text: reminderText(open) }],
      source: { kind: KIND, turn },
    }))
  })
}
