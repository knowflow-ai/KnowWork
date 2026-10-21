/**
 * 测试支撑：启动官方模拟模型服务与 KnowWork 宿主（真实 DSH 组合，不 mock 内核），
 * 并提供调用宿主 HTTP 接口、读取事件流的小工具。
 */

import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, cpSync, mkdirSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startMockLlmServer } from '@deepseek-ai/dsh-llm-mock-server'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const PROFILE_TEMPLATE = join(ROOT, 'profile')
const DSH_BIN = join(ROOT, 'node_modules', '.bin', 'dsh')
const TEST_OVERLAY = join(ROOT, 'tests', 'overlays', 'test-tools.yml')

/** 每个测试一个独立的 DSH_HOME，避免会话文件互相影响。 */
function freshDshHome() {
  const home = mkdtempSync(join(tmpdir(), 'kw-dsh-home-'))
  const profile = join(home, 'profiles', 'knowwork-host')
  mkdirSync(profile, { recursive: true })
  for (const file of ['package.json', 'cordis.yml', 'cordis.patch.yml', 'pnpm-workspace.yaml']) {
    cpSync(join(PROFILE_TEMPLATE, file), join(profile, file))
  }
  mkdirSync(join(profile, 'node_modules', '@knowwork'), { recursive: true })
  symlinkSync(join(ROOT, 'plugins', 'dsh-host'), join(profile, 'node_modules', '@knowwork', 'dsh-host'))
  symlinkSync(join(ROOT, 'plugins', 'host-bundle'), join(profile, 'node_modules', '@knowwork', 'dsh-host-bundle'))
  return home
}

/**
 * @param {{ sequence: string[], toolName?: string, toolArguments?: string, chunkDelayMs?: number, successText?: string }} mock
 * @param {Record<string, string>} [env]
 * @param {{ stdin?: 'pipe' | 'ignore', home?: string }} [options]
 */
export async function startHost(mock, env = {}, options = {}) {
  const llm = await startMockLlmServer({
    sequence: mock.sequence,
    repeatLast: true,
    toolName: mock.toolName,
    toolArguments: mock.toolArguments,
    chunkDelayMs: mock.chunkDelayMs,
    successText: mock.successText,
  })
  const token = randomBytes(24).toString('hex')
  const home = options.home ?? freshDshHome()
  const child = spawn(DSH_BIN, ['--profile', 'knowwork-host', '--patch', TEST_OVERLAY], {
    cwd: ROOT,
    env: {
      ...process.env,
      DSH_HOME: home,
      DEEPSEEK_BASE_URL: `${llm.baseURL}/v1`,
      DEEPSEEK_API_KEY: 'mock-key',
      KNOWWORK_HOST_TOKEN: token,
      KNOWWORK_HOST_PORT: '0',
      KNOWWORK_MODEL_PROVIDER: 'deepseek-official',
      KNOWWORK_MODEL: 'deepseek-chat',
      ...env,
    },
    stdio: [options.stdin ?? 'pipe', 'pipe', 'pipe'],
  })
  let stderr = ''
  let stdout = ''
  child.stderr.on('data', chunk => { stderr += chunk })
  child.stdout.on('data', chunk => { stdout += chunk })
  const port = await new Promise((resolvePort, reject) => {
    child.on('error', reject)
    let out = ''
    const timer = setTimeout(() => reject(new Error(`宿主 60 秒内未就绪\nstdout:${out}\nstderr:${stderr}`)), 60_000)
    child.stdout.on('data', chunk => {
      out += chunk
      for (const line of out.split('\n')) {
        try {
          const parsed = JSON.parse(line)
          if (parsed.knowworkHostReady) { clearTimeout(timer); resolvePort(parsed.port) }
        } catch { /* 非就绪行 */ }
      }
    })
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`宿主退出 code=${code}\nstderr:${stderr}`)) })
  })
  const base = `http://127.0.0.1:${port}`
  const call = async (method, path, body) => {
    const res = await fetch(`${base}${path}`, {
      method,
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const json = await res.json()
    return { status: res.status, body: json }
  }
  const stop = async ({ keepHome = false, signal = 'SIGTERM' } = {}) => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(r => child.once('exit', r))
      child.stdin?.end()
      child.kill(signal)
      await exited
    }
    await llm.close?.()
    if (!keepHome) rmSync(home, { recursive: true, force: true })
  }
  return { base, token, call, llm, stop, child, home, stderr: () => stderr, stdout: () => stdout }
}

/**
 * 订阅事件流，直到 predicate 返回 true 或超时；返回收到的全部帧。
 * @param {{ base: string, token: string }} host
 */
export async function collectEvents(host, sessionId, predicate, { after = 0, timeoutMs = 20_000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const frames = []
  try {
    const res = await fetch(`${host.base}/v1/sessions/${encodeURIComponent(sessionId)}/events?after=${after}`, {
      headers: { authorization: `Bearer ${host.token}` },
      signal: controller.signal,
    })
    const decoder = new TextDecoder()
    let buffer = ''
    for await (const chunk of res.body) {
      buffer += decoder.decode(chunk, { stream: true })
      let index
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)
        if (line.length === 0) continue
        const frame = JSON.parse(line)
        frames.push(frame)
        if (predicate(frame, frames)) return frames
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') throw error
    throw new Error(`等待事件超时，已收到：${frames.map(f => f.kind + ':' + (f.data?.type ?? '')).join(', ')}`)
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
  return frames
}

export const isTurnEnd = frame => frame.kind === 'session/event' && frame.data?.type === 'turn/end'
