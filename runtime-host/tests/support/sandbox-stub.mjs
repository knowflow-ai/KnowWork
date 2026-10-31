/**
 * 业务服务沙箱内部接口的测试桩（/internal/extensions/sandbox/{exec,stat,read,list,write}）。
 * 每个令牌对应一个会话；`/sessions/<会话>` 对到一个本地临时目录（扮演容器的 /workspace），exec 真的在那里跑。
 * 只为验证宿主侧的远程执行器接线，容器本身的行为由业务服务的测试负责。
 *
 * 给了 ssh.containers（会话 → 容器名）时另提供流式执行的两个接口，照业务服务的做法落到真实容器：
 * ssh-identity 读容器的主机公钥，WebSocket /ssh 装好临时公钥后接到 `docker exec -i <容器> sshd -i`。
 */

import { createServer } from 'node:http'
import { execFile, execFileSync, spawn } from 'node:child_process'
import { WebSocketServer } from 'ws'
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, posix } from 'node:path'

const SSHD = ['/usr/sbin/sshd', '-i', '-f', '/etc/ssh/sshd_config_kw']

/** 流式执行的两个接口（同业务服务的 ssh_pipe）：登录账号固定 root（沙箱默认的执行账号）。 */
function sshEndpoints(containers, sessionsByToken, requests) {
  const identity = session => {
    const container = containers[session]
    const [user, hostKey] = execFileSync('docker', ['exec', container, 'sh', '-c',
      'getent passwd root | cut -d: -f1; cut -d" " -f1-2 /etc/ssh/ssh_host_ed25519_key.pub'], { encoding: 'utf8' }).trim().split('\n')
    return { user, hostKey }
  }
  const wss = new WebSocketServer({ noServer: true })
  const upgrade = (req, socket, head) => {
    const session = sessionsByToken[(req.headers.authorization ?? '').replace(/^Bearer /, '')]
    const key = req.headers['x-knowwork-ssh-key']
    requests.push({ operation: 'ssh', session, key })
    if (session === undefined || !/^ssh-ed25519 [A-Za-z0-9+/]{68}$/.test(key ?? '')) return socket.destroy()
    const container = containers[session]
    execFileSync('docker', ['exec', container, 'sh', '-c',
      'umask 022; mkdir -p /etc/ssh/kw_authorized_keys && printf "%s\n" "$1" > /etc/ssh/kw_authorized_keys/root', 'kw', key])
    wss.handleUpgrade(req, socket, head, ws => {
      const sshd = spawn('docker', ['exec', '-i', container, ...SSHD], { stdio: ['pipe', 'pipe', 'ignore'] })
      sshd.stdout.on('data', chunk => ws.send(chunk))
      sshd.on('exit', () => ws.close())
      ws.on('message', data => sshd.stdin.write(data))
      ws.on('close', () => sshd.stdin.end())
    })
  }
  return { identity, upgrade }
}

export async function startSandboxStub({ sessionsByToken, ssh }) {
  const workspaces = new Map()
  const requests = []
  const workspaceOf = sessionId => {
    if (!workspaces.has(sessionId)) workspaces.set(sessionId, mkdtempSync(join(tmpdir(), 'kw-sbx-')))
    return workspaces.get(sessionId)
  }
  const local = (sessionId, path) => {
    const root = `/sessions/${sessionId}`
    const normal = posix.normalize(path)
    if (normal !== root && !normal.startsWith(`${root}/`)) return undefined
    return join(workspaceOf(sessionId), normal.slice(root.length))
  }
  const statOf = (file, follow) => {
    let info
    try {
      info = follow ? statSync(file) : lstatSync(file)
    } catch {
      return null
    }
    const kind = info.isSymbolicLink() ? 'l' : info.isFile() ? 'f' : info.isDirectory() ? 'd' : 'o'
    return { kind, size: info.size, version: `${info.mtimeMs}:${info.ino}:${info.size}` }
  }

  const handlers = {
    exec: (session, body) => new Promise(resolve => {
      const cwd = local(session, body.cwd)
      mkdirSync(cwd, { recursive: true })
      execFile(body.argv[0], body.argv.slice(1), { cwd, env: { PATH: process.env.PATH, ...body.env } }, (error, stdout, stderr) => {
        resolve({ exitCode: error ? (typeof error.code === 'number' ? error.code : 1) : 0, stdout, stderr, timedOut: false, truncated: false })
      })
    }),
    stat: (session, body) => ({ stat: statOf(local(session, body.path), body.follow !== false) }),
    // 同业务服务的 read：顺带返回 stat（版本号与 stat 一致），不存在、是目录照 stat 的错误码报
    read: (session, body) => {
      const file = local(session, body.path)
      const stat = statOf(file, true)
      if (stat === null) throw Object.assign(new Error('文件不存在'), { code: 'not_found' })
      if (stat.kind === 'd') throw Object.assign(new Error('该路径是目录'), { code: 'is_directory', status: 400 })
      const data = readFileSync(file)
      return { size: data.length, data: data.subarray(body.offset, body.offset + body.length).toString('base64'), stat }
    },
    list: (session, body) => ({
      entries: readdirSync(local(session, body.path)).sort().map(name => {
        const stat = statOf(join(local(session, body.path), name), false)
        return { name, kind: stat.kind, size: stat.size }
      }),
    }),
    write: (session, body) => {
      const file = local(session, body.path)
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, Buffer.from(body.data, 'base64'))
      return { stat: statOf(file, true) }
    },
  }

  const streaming = ssh === undefined ? undefined : sshEndpoints(ssh.containers, sessionsByToken, requests)
  if (streaming !== undefined) handlers['ssh-identity'] = session => streaming.identity(session)

  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    const operation = req.url.split('/').pop()
    const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
    const session = sessionsByToken[token]
    requests.push({ operation, token, body })
    const reply = (status, payload) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)) }
    if (session === undefined) return reply(401, { error: { code: 'unauthorized', message: '令牌无效' } })
    if (body.path !== undefined && local(session, body.path) === undefined) {
      return reply(403, { error: { code: 'permission_denied', message: '只能访问本会话工作目录下的路径' } })
    }
    try {
      reply(200, await handlers[operation](session, body))
    } catch (error) {
      reply(error.status ?? 404, { error: { code: error.code ?? 'not_found', message: String(error.message) } })
    }
  })
  if (streaming !== undefined) server.on('upgrade', streaming.upgrade)
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}/internal/extensions/sandbox`,
    requests,
    workspaceOf,
    close: () => new Promise(resolve => server.close(resolve)),
  }
}
