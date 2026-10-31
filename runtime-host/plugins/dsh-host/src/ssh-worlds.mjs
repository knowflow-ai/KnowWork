/**
 * 流式执行：按会话打开 DSH 官方的 SSH 执行环境（dsh-ssh + dsh-subprocess-ssh），连进本会话容器里的 `sshd -i`。
 * 执行中要来回通话的官方能力（PTC 的 fd 7 控制管道、终端、stdin/stdout 管道）经它落到会话容器；收集模式的命令
 * （官方 bash）照旧走业务服务的执行接口（remote-subprocess.mjs），技能环境变量只在那条路上注入。
 *
 * 一个执行环境由这几样拼成，都是 OpenSSH 与 DSH 官方的现成能力：
 * - 主机别名 kw-<会话摘要>：私有 OpenSSH 配置里一条通配规则，ProxyCommand 用 `nc -U` 接到宿主自己监听的 Unix 套接字，
 *   宿主再经业务服务的 WebSocket（内部端口 /internal/extensions/sandbox/ssh，带本会话令牌）接到容器里的 sshd -i。
 *   令牌只在宿主进程里，不落文件；
 * - 官方 dsh-ssh 只调 PATH 里的 `ssh`、不能传 -F：第一次用到时在私有临时目录放一个包装脚本指定配置文件，
 *   放到本进程 PATH 最前面。宿主本身不起本地进程，这只影响官方 dsh-ssh 起的 ssh；
 * - 登录用公钥：每个执行环境现生成一对，私钥 0600 放私有目录，公钥随握手交给业务服务装进容器；主机公钥经业务服务的
 *   ssh-identity 取来写进私有 known_hosts（官方要求严格校验主机密钥），登录账号与沙箱的执行账号一致；
 * - 每个会话在本插件的上下文里 isolate 一份 ssh 与 subprocess 服务：互不覆盖，也不顶替全局的 ctx.subprocess；
 * - 容器里的辅助程序与 PTC 启动脚本在 /opt/dsh-ssh，摘要按本机同版本的文件算，对不上官方拒绝连接
 *   （沙箱镜像与宿主的 DSH 版本必须一致）。
 * 执行环境按需打开（会话第一次用到管道、控制管道或终端），会话释放时关掉，也会在没有进程在跑、空闲超过
 * KNOWWORK_HOST_SSH_IDLE_MINUTES（默认 10 分钟）时自己关掉：每个打开的环境在容器里多占约 85 MB，而且管道开着时
 * 业务服务把容器算作「正在用」，空闲清扫不收，不关的话部署方关掉宿主的会话释放后容器会一直留着。
 */

import { createHash } from 'node:crypto'
import { accessSync, chmodSync, constants, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { delimiter, dirname, join } from 'node:path'
import { Duplex, PassThrough } from 'node:stream'
import { fileURLToPath } from 'node:url'
import SshConnection from '@deepseek-ai/dsh-ssh'
import SshSubprocessRuntime from '@deepseek-ai/dsh-subprocess-ssh'
import { createSandboxClient, sessionRoot } from './sandbox-client.mjs'
import { generateSshKey } from './ssh-key.mjs'
import { getSession } from './state.mjs'

const REMOTE_PACKAGES = '/opt/dsh-ssh/node_modules/@deepseek-ai'
export const REMOTE_NODE = '/usr/local/bin/node'
export const REMOTE_HELPER = `${REMOTE_PACKAGES}/dsh-ssh/lib/helper.js`
export const REMOTE_PTC_BOOTSTRAP = `${REMOTE_PACKAGES}/dsh-ptc-runtime-node/lib/process.js`
const READY_TIMEOUT_MS = 30_000
const DEFAULT_IDLE_MINUTES = 10
const MAX_BUFFERED = 4 * 1024 * 1024
const DRAIN_POLL_MS = 10
const SERVICE_POLL_MS = 20

function localFile(pkg, file) {
  return join(dirname(fileURLToPath(import.meta.resolve(pkg))), file)
}

const sha256File = path => createHash('sha256').update(readFileSync(path)).digest('hex')

/**
 * 主机别名：会话 id 里可能有冒号，OpenSSH 别名与官方配置都不收，用摘要；再加实例序号，重开时（旧环境还在异步关）
 * 新旧两个环境的 Unix 套接字、私钥文件、known_hosts 行互不相撞。
 */
export function aliasOf(sessionId, serial) {
  return `kw-${createHash('sha256').update(String(sessionId)).digest('hex').slice(0, 20)}-${serial}`
}

/** 执行环境空闲多久关（毫秒）；允许小数分钟（测试用）。 */
export function worldIdleMsOf(raw) {
  if (raw === undefined || raw === '') return DEFAULT_IDLE_MINUTES * 60_000
  const minutes = Number(raw)
  if (!Number.isFinite(minutes) || minutes <= 0) {
    throw new Error(`KNOWWORK_HOST_SSH_IDLE_MINUTES 必须是大于 0 的数，当前为 ${raw}`)
  }
  return Math.round(minutes * 60_000)
}

function which(command, skipDir) {
  for (const dir of (process.env.PATH ?? '').split(delimiter)) {
    if (!dir || dir === skipDir) continue
    const candidate = join(dir, command)
    try {
      accessSync(candidate, constants.X_OK)
      return candidate
    } catch { /* 下一个 */ }
  }
  return undefined
}

/**
 * 私有目录、ssh 包装脚本与配置。只在第一次用到流式执行时建。
 * - 配置、known_hosts、私钥、Unix 套接字放 /tmp 下的私有目录：套接字路径有长度上限（macOS 104 字节），官方 dsh-ssh
 *   的控制套接字也放这里；容器部署里 /tmp 是内存里的 tmpfs，私钥不落盘；
 * - 包装脚本要能执行，而容器部署的 /tmp 挂成 noexec、根文件系统只读，所以放 DSH_HOME（数据卷）下，每次启动重写。
 */
function prepareClient() {
  const dir = mkdtempSync('/tmp/kw-ssh-')
  const bin = process.env.DSH_HOME ? join(process.env.DSH_HOME, 'knowwork-ssh-bin') : join(dir, 'bin')
  const ssh = which('ssh', bin)
  const nc = which('nc', bin)
  if (ssh === undefined || nc === undefined) {
    rmSync(dir, { recursive: true, force: true })
    throw new Error('流式执行需要宿主机装 OpenSSH 客户端（ssh）与支持 -U 的 netcat（nc）')
  }
  for (const sub of ['k', 's']) mkdirSync(join(dir, sub), { mode: 0o700 })
  rmSync(bin, { recursive: true, force: true })
  mkdirSync(bin, { recursive: true, mode: 0o700 })
  writeFileSync(join(dir, 'known_hosts'), '', { mode: 0o600 })
  writeFileSync(join(dir, 'config'), [
    'Host kw-*',
    `  ProxyCommand ${nc} -U ${join(dir, 's')}/%h`,
    `  IdentityFile ${join(dir, 'k')}/%h`,
    '  IdentitiesOnly yes',
    `  UserKnownHostsFile ${join(dir, 'known_hosts')}`,
    '  GlobalKnownHostsFile /dev/null',
    '  StrictHostKeyChecking yes',
    '  PasswordAuthentication no',
    '  KbdInteractiveAuthentication no',
    '  GSSAPIAuthentication no',
    '  LogLevel ERROR',
    '',
  ].join('\n'), { mode: 0o600 })
  writeFileSync(join(bin, 'ssh'), `#!/bin/sh\nexec ${ssh} -F ${join(dir, 'config')} "$@"\n`, { mode: 0o700 })
  process.env.PATH = `${bin}${delimiter}${process.env.PATH ?? ''}`
  return {
    dir,
    helperHash: sha256File(localFile('@deepseek-ai/dsh-ssh', 'helper.js')),
    bootstrapHash: sha256File(localFile('@deepseek-ai/dsh-ptc-runtime-node', 'process.js')),
    dispose() {
      process.env.PATH = (process.env.PATH ?? '').split(delimiter).filter(entry => entry !== bin).join(delimiter)
      rmSync(dir, { recursive: true, force: true })
      rmSync(bin, { recursive: true, force: true })
    },
  }
}

/** 本地 Unix 套接字 ⇄ 业务服务的 WebSocket，原样转发字节。 */
function bridge(socket, wsUrl, token, publicKey) {
  const ws = new WebSocket(wsUrl, { headers: { authorization: `Bearer ${token}`, 'x-knowwork-ssh-key': publicKey } })
  ws.binaryType = 'arraybuffer'
  const pending = []
  let drain
  socket.on('data', chunk => {
    if (ws.readyState !== WebSocket.OPEN) {
      pending.push(chunk)
      return
    }
    ws.send(chunk)
    if (ws.bufferedAmount > MAX_BUFFERED && drain === undefined) {
      socket.pause()
      drain = setInterval(() => {
        if (ws.bufferedAmount > MAX_BUFFERED / 2 && ws.readyState === WebSocket.OPEN) return
        clearInterval(drain)
        drain = undefined
        socket.resume()
      }, DRAIN_POLL_MS)
    }
  })
  ws.addEventListener('open', () => { for (const chunk of pending.splice(0)) ws.send(chunk) })
  ws.addEventListener('message', event => socket.write(Buffer.from(event.data)))
  ws.addEventListener('close', () => socket.end())
  ws.addEventListener('error', () => socket.destroy())
  socket.on('error', () => {})
  socket.on('close', () => {
    if (drain !== undefined) clearInterval(drain)
    if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) ws.close()
  })
}

/**
 * 等 isolate 出来的子上下文里出现某个服务。用 ctx.reflect.get 按子上下文自己的 isolate 映射取：宿主的组合是严格模式，
 * 在 inject 回调里读 isolate 掉的服务属性会被拦（cannot get property without inject）。
 * strict 为 true 时等到服务就绪；false 时注册了就返回（用来拿 ssh 连接对象，再等它的 ready 拿到真正的失败原因）。
 */
async function serviceOf(ctx, name, timeoutMs, strict = true) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const service = ctx.reflect.get(name, strict)
    if (service !== undefined) return service
    if (Date.now() >= deadline) throw new Error(`流式执行环境没有就绪（${name}）`)
    await new Promise(resolve => setTimeout(resolve, SERVICE_POLL_MS))
  }
}

/**
 * 执行环境还没就绪时的句柄：先把要的流交出去，就绪后接到真实句柄上（官方 subprocess-ssh 自己也是先交句柄再连流）。
 * @param {Promise<{ subprocess: any }>} ready
 */
function deferredHandle(ready, spec) {
  const { stdio } = spec
  const stdin = stdio.stdin === 'pipe' ? new PassThrough() : undefined
  const stdout = stdio.stdout === 'pipe' ? new PassThrough() : undefined
  const stderr = stdio.stderr === 'pipe' ? new PassThrough() : undefined
  const toControl = stdio.control === 'pipe' ? new PassThrough() : undefined
  const fromControl = stdio.control === 'pipe' ? new PassThrough() : undefined
  const control = toControl === undefined ? undefined : Duplex.from({ writable: toControl, readable: fromControl })
  let real
  let terminated = false
  const attached = ready.then(world => {
    if (terminated || spec.signal?.aborted) return undefined
    real = world.subprocess.spawn(spec)
    if (stdin !== undefined) stdin.pipe(real.stdin)
    if (stdout !== undefined) real.stdout.pipe(stdout)
    if (stderr !== undefined) real.stderr.pipe(stderr)
    if (control !== undefined) {
      toControl.pipe(real.control)
      real.control.pipe(fromControl)
    }
    return real
  })
  attached.catch(error => {
    for (const stream of [stdout, stderr, fromControl]) stream?.destroy(error)
  })
  const reader = name => ({
    readFrom(offset) {
      return real?.collected?.[name]?.readFrom(offset) ?? { text: '', nextOffset: offset, lossy: false }
    },
  })
  return {
    stdin, stdout, stderr, control,
    collected: { stdout: typeof stdio.stdout === 'object' ? reader('stdout') : undefined,
      stderr: typeof stdio.stderr === 'object' ? reader('stderr') : undefined },
    done: attached.then(handle => handle === undefined ? { exitCode: null, signal: 'SIGKILL' } : handle.done),
    terminate() {
      terminated = true
      real?.terminate()
    },
    async waitForExit(signal) {
      const handle = await attached.catch(() => undefined)
      return handle === undefined ? true : handle.waitForExit(signal)
    },
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx 本插件的上下文（每个会话从它 isolate）
 * @param {{ baseUrl?: string }} [options] 业务服务沙箱接口的地址（同 sandbox-client）
 */
export function createSshWorlds(ctx, options = {}) {
  const base = (options.baseUrl ?? process.env.KNOWWORK_SANDBOX_BASE_URL
    ?? 'http://127.0.0.1:8001/internal/extensions/sandbox').replace(/\/+$/, '')
  const wsUrl = `${base.replace(/^http/, 'ws')}/ssh`
  const client = createSandboxClient({ baseUrl: base })
  const worlds = new Map() // 会话 id → { promise, world?, active, lastUsed }
  const knownHosts = new Map()
  const idleMs = worldIdleMsOf(process.env.KNOWWORK_HOST_SSH_IDLE_MINUTES)
  let local
  let serial = 0
  let sweeper

  function writeKnownHosts() {
    const file = join(local.dir, 'known_hosts')
    writeFileSync(`${file}.tmp`, [...knownHosts].map(([alias, key]) => `${alias} ${key}\n`).join(''), { mode: 0o600 })
    renameSync(`${file}.tmp`, file)
  }

  async function open(sessionId) {
    local ??= prepareClient()
    const alias = aliasOf(sessionId, ++serial)
    const identity = await client.request('ssh-identity', sessionId, {})
    const key = generateSshKey(alias)
    writeFileSync(join(local.dir, 'k', alias), key.privateKey, { mode: 0o600 })
    knownHosts.set(alias, identity.hostKey)
    writeKnownHosts()
    const socketPath = join(local.dir, 's', alias)
    const server = createServer(socket => {
      const token = getSession(sessionId)?.toolAccess?.token
      if (token === undefined) socket.destroy()
      else bridge(socket, wsUrl, token, key.publicKey)
    })
    await new Promise((resolve, reject) => server.once('error', reject).listen(socketPath, resolve))
    chmodSync(socketPath, 0o600)

    const fork = ctx.isolate('ssh').isolate('subprocess')
    const fibers = [
      fork.plugin(SshConnection, {
        host: `${identity.user}@${alias}`,
        launch: { kind: 'node-script', node: REMOTE_NODE, bootstrapPath: REMOTE_PTC_BOOTSTRAP, bootstrapHash: local.bootstrapHash },
        helper: REMOTE_HELPER, helperHash: local.helperHash, workspace: sessionRoot(sessionId),
      }),
      fork.plugin(SshSubprocessRuntime, {}),
    ]
    const cleanup = async () => {
      for (const fiber of fibers.reverse()) await Promise.resolve(fiber.dispose?.()).catch(() => {})
      await new Promise(resolve => server.close(() => resolve()))
      rmSync(join(local.dir, 'k', alias), { force: true })
      knownHosts.delete(alias)
      writeKnownHosts()
    }
    try {
      const ssh = await serviceOf(fork, 'ssh', READY_TIMEOUT_MS, false)
      await Promise.race([ssh.ready, new Promise((_, reject) => setTimeout(() => reject(new Error('流式执行环境连接超时')), READY_TIMEOUT_MS))])
      const subprocess = await serviceOf(fork, 'subprocess', READY_TIMEOUT_MS)
      return { ssh, subprocess, close: cleanup }
    } catch (error) {
      await cleanup()
      throw error
    }
  }

  /** 没有进程在跑、空闲超过 idleMs 的执行环境关掉。第一次打开执行环境时才起这个定时器。 */
  function sweepIdle() {
    const now = Date.now()
    for (const [sessionId, current] of worlds) {
      if (current.world !== undefined && current.active === 0 && now - current.lastUsed >= idleMs) {
        close(sessionId).catch(() => {})
      }
    }
  }

  /** 记下一个进程或终端：它结束前执行环境不算空闲。 */
  function track(current, done) {
    current.active += 1
    current.lastUsed = Date.now()
    Promise.resolve(done).catch(() => {}).finally(() => {
      current.active -= 1
      current.lastUsed = Date.now()
    })
  }

  function entry(sessionId) {
    if (sweeper === undefined) {
      sweeper = setInterval(sweepIdle, Math.min(60_000, Math.max(50, Math.floor(idleMs / 2))))
      sweeper.unref()
    }
    let current = worlds.get(sessionId)
    // 官方连接不重连：断过（业务服务重启、容器被删）就整个关掉重开。failure 是 dsh-ssh 连接对象上记的首个失败原因
    if (current?.world?.ssh.failure !== undefined) {
      worlds.delete(sessionId)
      current.world.close().catch(() => {})
      current = undefined
    }
    if (current === undefined) {
      current = { promise: open(sessionId), active: 0, lastUsed: Date.now() }
      current.promise.then(world => { current.world = world }, () => {
        if (worlds.get(sessionId) === current) worlds.delete(sessionId) // 失败不缓存，下次重试
      })
      worlds.set(sessionId, current)
    }
    return current
  }

  async function close(sessionId) {
    const current = worlds.get(sessionId)
    if (current === undefined) return
    worlds.delete(sessionId)
    const world = await current.promise.catch(() => undefined)
    await world?.close()
  }

  return {
    /** 同步 spawn：已就绪直接交给官方提供方，否则先交占位句柄。 */
    spawn(sessionId, spec) {
      const current = entry(sessionId)
      const handle = current.world !== undefined ? current.world.subprocess.spawn(spec) : deferredHandle(current.promise, spec)
      track(current, handle.done)
      return handle
    },
    /** 终端分配本来就是异步的：等执行环境就绪再交给官方提供方。 */
    async spawnTerminal(sessionId, spec) {
      const current = entry(sessionId)
      const world = await current.promise
      const terminal = await world.subprocess.spawnTerminal(spec)
      track(current, terminal.done)
      return terminal
    },
    close,
    async closeAll() {
      if (sweeper !== undefined) clearInterval(sweeper)
      sweeper = undefined
      await Promise.all([...worlds.keys()].map(close))
      local?.dispose()
      local = undefined
    },
  }
}
