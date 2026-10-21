/**
 * E1 用的测试工具：只读、需要审批的写入、作为完成判据的收尾工具。
 * 正式产品里这些由业务服务经工具桥提供，这里只为验证扩展点。
 */

import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'knowwork-test-tools'
export const inject = ['tools']

const text = (_args, value) => [{ type: 'text', text: String(value) }]

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'kw_echo',
    description: '原样返回输入的文字。',
    parameters: { text: { type: 'string', required: true, description: '要返回的文字' } },
    output: { schema: { type: 'string' }, render: text },
    async execute(args) { return `echo: ${args.text}` },
  }))
  ctx.tools.register(defineTool({
    name: 'kw_write',
    description: '把一段文字写入共享目录（需要用户确认）。',
    parameters: { text: { type: 'string', required: true, description: '要写入的文字' } },
    output: { schema: { type: 'string' }, render: text },
    async execute(args) { return `written: ${args.text}` },
  }))
  ctx.tools.register(defineTool({
    name: 'kw_finalize',
    description: '提交最终结果前的检查。',
    parameters: { summary: { type: 'string', required: true, description: '结果摘要' } },
    output: { schema: { type: 'string' }, render: text },
    async execute(args) { return `finalized: ${args.summary}` },
  }))
  ctx.tools.register(defineTool({
    name: 'kw_sleep',
    description: '等待指定毫秒数，用于测试长工具。',
    parameters: { ms: { type: 'number', required: true, description: '等待毫秒数' } },
    output: { schema: { type: 'string' }, render: text },
    async execute(args, exec) {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, args.ms)
        exec.signal.addEventListener('abort', () => { clearTimeout(timer); reject(exec.signal.reason ?? new Error('aborted')) }, { once: true })
      })
      return `slept ${args.ms}`
    },
  }))
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (exec.name === 'kw_write') return { kind: 'ask', reason: '写入共享目录需要确认' }
    return next()
  })
}
