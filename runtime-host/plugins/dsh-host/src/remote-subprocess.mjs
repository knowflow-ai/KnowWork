/**
 * 远程 `ctx.subprocess`：宿主里所有子进程都落到会话容器里执行，宿主机上不起任何进程。
 *
 * 官方 bash 执行器（dsh-bash-local）在这层之上实现超时、取消、输出截断与后台任务，这里只负责「在哪跑」：
 * - 会话按 cwd 认（`/sessions/<会话 id>/...`，见 sandbox-client.mjs），cwd 不在会话目录下直接拒绝；
 * - 只支持收集模式的输出（官方 bash 执行器就用这种）；不支持 stdin（模型的 bash 调用不给 stdin）；
 * - 输出在命令结束时一次拿回（第一版不做流式），读者按偏移读、互不消费；
 * - 取消与超时：调用方的 signal 一触发就中止请求，业务服务随之整组杀掉容器里的进程；
 * - 环境变量只带调用方显式给的，宿主机路径类的 DSH_HOME、DSH_PROFILE_DIR 不往容器里带；
 * - 不提供终端会话（spawnTerminal）：宿主已停用持久化 bash 与终端工具。
 */

import { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess'
import { createSandboxClient, sessionPath } from './sandbox-client.mjs'

const MAX_TIMEOUT_MS = 3_600_000
const HOST_ONLY_ENV = new Set(['DSH_HOME', 'DSH_PROFILE_DIR'])

function collectLimit(mode, stream) {
  if (typeof mode !== 'object' || mode === null || typeof mode.maxBytes !== 'number') {
    throw new Error(`remote subprocess: ${stream} must use collected output`)
  }
  return mode.maxBytes
}

/** 命令结束前读到空，结束后读到全文；按字节偏移，互不消费（SubprocessOutputReader 合同）。 */
function reader(state, stream) {
  return {
    readFrom(fromByte) {
      const bytes = state[stream]
      if (bytes === undefined) return { text: '', nextOffset: fromByte, lossy: false }
      const start = Math.min(Math.max(0, fromByte), bytes.length)
      return { text: bytes.subarray(start).toString('utf8'), nextOffset: bytes.length, lossy: state.truncated && start === 0 }
    },
  }
}

function envOf(env) {
  const out = {}
  for (const [key, value] of Object.entries(env ?? {})) {
    if (typeof value === 'string' && !HOST_ONLY_ENV.has(key)) out[key] = value
  }
  return out
}

export default class RemoteSubprocessRuntime extends SubprocessRuntime {
  constructor(ctx, config = {}) {
    super(ctx)
    this.client = createSandboxClient({ baseUrl: config.baseUrl })
    this.running = new Set()
  }

  async resolveExecutable(command) {
    if (typeof command !== 'string' || command.length === 0) throw new Error('remote subprocess: empty command')
    if (command.includes('/') && !command.startsWith('/')) throw new Error('remote subprocess: relative executable paths are not supported')
    return command
  }

  async terminalEnvironment() {
    return { platform: 'linux', shell: 'bash' }
  }

  async spawnTerminal() {
    throw new Error('remote subprocess: terminal sessions are not supported')
  }

  spawn(spec) {
    if (spec.signal?.aborted) throw new Error('remote subprocess: aborted before spawn')
    if (!Array.isArray(spec.argv) || spec.argv.length === 0) throw new Error('remote subprocess: argv must not be empty')
    const located = sessionPath(spec.cwd)
    if (located === undefined) throw new Error(`remote subprocess: cwd must be inside the session working directory: ${spec.cwd}`)
    if (spec.stdio.stdin !== 'ignore' && !(typeof spec.stdio.stdin === 'object' && spec.stdio.stdin.data === '')) {
      throw new Error('remote subprocess: stdin is not supported')
    }
    const maxOutputBytes = Math.max(collectLimit(spec.stdio.stdout, 'stdout'), collectLimit(spec.stdio.stderr, 'stderr'))

    const state = { stdout: undefined, stderr: undefined, truncated: false }
    const controller = new AbortController()
    const signal = spec.signal === undefined ? controller.signal : AbortSignal.any([spec.signal, controller.signal])
    const done = this.client.request('exec', located.sessionId, {
      argv: [...spec.argv], cwd: located.path, timeoutMs: MAX_TIMEOUT_MS, maxOutputBytes, env: envOf(spec.env),
    }, { signal, timeoutMs: MAX_TIMEOUT_MS + 30_000 }).then(result => {
      state.stdout = Buffer.from(result.stdout ?? '', 'utf8')
      state.stderr = Buffer.from(result.stderr ?? '', 'utf8')
      state.truncated = result.truncated === true
      return { exitCode: typeof result.exitCode === 'number' && result.exitCode >= 0 ? result.exitCode : null,
        signal: result.timedOut ? 'SIGKILL' : null }
    }, error => {
      if (signal.aborted) {
        state.stdout ??= Buffer.alloc(0)
        state.stderr ??= Buffer.alloc(0)
        return { exitCode: null, signal: 'SIGKILL' }
      }
      throw error
    })
    this.running.add(done)
    const settled = done.then(() => undefined, () => undefined).finally(() => this.running.delete(done))

    return {
      stdin: undefined, stdout: undefined, stderr: undefined, control: undefined,
      collected: { stdout: reader(state, 'stdout'), stderr: reader(state, 'stderr') },
      done,
      terminate: () => controller.abort(),
      async waitForExit(waitSignal) {
        if (waitSignal === undefined) {
          await settled
          return true
        }
        return Promise.race([settled.then(() => true), new Promise(resolve => {
          if (waitSignal.aborted) resolve(false)
          waitSignal.addEventListener('abort', () => resolve(false), { once: true })
        })])
      },
    }
  }
}

export const name = 'knowwork-remote-subprocess'
