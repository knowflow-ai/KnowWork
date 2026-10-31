/**
 * 远程 `ctx.subprocess`：宿主里所有子进程都落到会话容器里执行，模型发起的进程一律不在宿主机上跑。
 *
 * 按输出方式分两条路：
 * - 收集模式（官方 bash 执行器就用这种，输出在命令结束时一次拿回）：经业务服务的执行接口。官方执行器在这层之上
 *   实现超时、取消、输出截断与后台任务；业务服务据宿主报的「这个会话加载过的技能」加上技能的环境变量
 *   （值不经过宿主），并把输出里的值换成 ******；
 * - 执行中要来回通话的（stdin / stdout / stderr 管道、fd 7 控制管道、终端）：交给本会话的 DSH 官方 SSH 执行环境
 *   （ssh-worlds.mjs），PTC、持久 shell 才用得了。这条路上业务服务只转发加密的字节，所以不注入技能环境变量。
 *   业务服务没打开流式执行时这条路报错，收集模式不受影响。
 * 两条路共用的规矩：
 * - 会话按 cwd 认（`/sessions/<会话 id>/...`，见 sandbox-client.mjs），cwd 不在会话目录下直接拒绝；
 *   模型的 bash 能自己给 workdir，所以还要核对调用方：官方 shell 环境给每次模型调用带上 DSH_SESSION_ID（保留键，
 *   模型改不了），它所属的根会话必须就是 cwd 里的会话，否则知道别人对话 id 就能在别人的容器里执行；
 * - 环境变量只带调用方显式给的，宿主机路径类的 DSH_HOME、DSH_PROFILE_DIR 不往容器里带；
 * - 收集模式不支持 stdin（模型的 bash 调用不给 stdin）；取消与超时：调用方的 signal 一触发就中止，
 *   容器里整组杀掉。
 */

import { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess'
import { createSandboxClient, sessionPath } from './sandbox-client.mjs'
import { createSshWorlds } from './ssh-worlds.mjs'
import { getSession, onSessionDropped, rootSessionIdOf } from './state.mjs'

const MAX_TIMEOUT_MS = 3_600_000
const HOST_ONLY_ENV = new Set(['DSH_HOME', 'DSH_PROFILE_DIR'])

const collected = mode => typeof mode === 'object' && mode !== null && typeof mode.maxBytes === 'number'

/** 执行中要来回通话的：任一路是管道或继承、有控制管道、或者给了非空 stdin。 */
function streaming(stdio) {
  const quietStdin = stdio.stdin === 'ignore' || (typeof stdio.stdin === 'object' && stdio.stdin.data === '')
  return !quietStdin || !collected(stdio.stdout) || !collected(stdio.stderr) || stdio.control !== undefined
}

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
    this.config = config
    this.client = createSandboxClient({ baseUrl: config.baseUrl })
    this.running = new Set()
    this.worlds = undefined // 第一次流式执行时才建（没用到就不起 ssh、不写任何文件）
    ctx.effect(() => onSessionDropped(sessionId => { this.worlds?.close(sessionId) }))
    ctx.effect(() => () => this.worlds?.closeAll())
  }

  ssh() {
    this.worlds ??= createSshWorlds(this.ctx, { baseUrl: this.config.baseUrl })
    return this.worlds
  }

  /** 认出 cwd 所在的会话，并核对调用方只能落到自己根会话的工作目录。 */
  locate(spec) {
    const located = sessionPath(spec.cwd)
    if (located === undefined) throw new Error(`remote subprocess: cwd must be inside the session working directory: ${spec.cwd}`)
    const caller = spec.env?.DSH_SESSION_ID
    if (caller !== undefined && rootSessionIdOf(caller) !== located.sessionId) {
      throw new Error(`remote subprocess: cwd must be inside the caller's own session working directory: ${spec.cwd}`)
    }
    return located
  }

  async resolveExecutable(command) {
    if (typeof command !== 'string' || command.length === 0) throw new Error('remote subprocess: empty command')
    if (command.includes('/') && !command.startsWith('/')) throw new Error('remote subprocess: relative executable paths are not supported')
    return command
  }

  async terminalEnvironment() {
    return { platform: 'linux', shell: 'bash' }
  }

  async spawnTerminal(spec) {
    const located = this.locate(spec)
    return this.ssh().spawnTerminal(located.sessionId, { ...spec, env: envOf(spec.env) })
  }

  spawn(spec) {
    if (spec.signal?.aborted) throw new Error('remote subprocess: aborted before spawn')
    if (!Array.isArray(spec.argv) || spec.argv.length === 0) throw new Error('remote subprocess: argv must not be empty')
    const located = this.locate(spec)
    if (streaming(spec.stdio)) return this.ssh().spawn(located.sessionId, { ...spec, env: envOf(spec.env) })
    const maxOutputBytes = Math.max(collectLimit(spec.stdio.stdout, 'stdout'), collectLimit(spec.stdio.stderr, 'stderr'))

    const state = { stdout: undefined, stderr: undefined, truncated: false }
    const controller = new AbortController()
    const signal = spec.signal === undefined ? controller.signal : AbortSignal.any([spec.signal, controller.signal])
    const skills = [...(getSession(located.sessionId)?.loadedSkills ?? [])]
    const done = this.client.request('exec', located.sessionId, {
      argv: [...spec.argv], cwd: located.path, timeoutMs: MAX_TIMEOUT_MS, maxOutputBytes, env: envOf(spec.env),
      ...(skills.length > 0 ? { skills } : {}),
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
