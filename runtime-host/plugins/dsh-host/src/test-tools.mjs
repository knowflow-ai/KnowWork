/**
 * E1 用的测试工具：只读、需要审批的写入、作为完成判据的收尾工具。
 * 正式产品里这些由业务服务经工具桥提供，这里只为验证扩展点。
 * 另有三个流式执行的探针（PTC、管道、终端），只在挂了 PTC 运行时与流式执行的测试里用。
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
  ctx.inject(['workingDirectory', 'subprocess', 'ptcRuntime'], scoped => registerStreamProbes(scoped))
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (exec.name === 'kw_write') return { kind: 'ask', reason: '写入共享目录需要确认' }
    return next()
  })
}

/** 流式执行的探针：都在本会话的工作目录里跑，结果原样交给模型（测试读它）。 */
function registerStreamProbes(ctx) {
  const cwdOf = exec => ctx.workingDirectory.ensure(exec.agent, exec.signal)
  ctx.tools.register(defineTool({
    name: 'kw_ptc_probe',
    description: '在会话容器里跑一段 PTC 程序，程序回调宿主函数。',
    parameters: {},
    output: { schema: { type: 'string' }, render: text },
    async execute(_args, exec) {
      const spec = ctx.ptcRuntime.resolve({
        program: 'const n = await host.add({ a: 2, b: 3 }); const fs = await import("node:fs"); '
          + 'return { n, inContainer: fs.existsSync("/opt/dsh-ssh"), cwd: process.cwd() }',
        bindings: [{ global: 'host', functions: { add: async ({ a, b }) => a + b } }],
        cwd: await cwdOf(exec),
      })
      const result = await ctx.ptcRuntime.run(spec)
      return JSON.stringify(result.error === undefined ? { value: result.value } : { error: result.error })
    },
  }))
  ctx.tools.register(defineTool({
    name: 'kw_pipe_probe',
    description: '在会话容器里起 cat，边写边读。',
    parameters: {},
    output: { schema: { type: 'string' }, render: text },
    async execute(_args, exec) {
      const handle = ctx.subprocess.spawn({ argv: ['/bin/cat'], cwd: await cwdOf(exec), graceMs: 2000,
        stdio: { stdin: 'pipe', stdout: 'pipe', stderr: { maxBytes: 4096 } } })
      const echoed = new Promise(resolve => {
        let seen = ''
        handle.stdout.on('data', chunk => {
          seen += chunk.toString('utf8')
          if (seen.includes('ping')) resolve(seen)
        })
      })
      handle.stdin.write('ping\n')
      const seen = await echoed
      handle.stdin.end()
      const outcome = await handle.done
      return JSON.stringify({ seen: seen.trim(), exitCode: outcome.exitCode })
    },
  }))
  ctx.tools.register(defineTool({
    name: 'kw_terminal_probe',
    description: '在会话容器里开一个终端，两次输入之间保留 cd。',
    parameters: {},
    output: { schema: { type: 'string' }, render: text },
    async execute(_args, exec) {
      const terminal = await ctx.subprocess.spawnTerminal({ argv: ['/bin/bash', '--norc', '-i'], cwd: await cwdOf(exec),
        rows: 24, cols: 100, terminalType: 'xterm-256color', graceMs: 2000 })
      try {
        const kept = new Promise(resolve => {
          let seen = ''
          terminal.output.on('data', chunk => {
            seen += chunk.toString('utf8')
            if (seen.includes('kept:/tmp')) resolve(true)
          })
        })
        await terminal.write('cd /tmp\n')
        await terminal.write('echo "kept:$(pwd)"\n')
        return JSON.stringify({ kept: await kept })
      } finally {
        await terminal.terminate()
      }
    },
  }))
}
