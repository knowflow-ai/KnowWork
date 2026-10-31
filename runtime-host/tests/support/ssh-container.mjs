/**
 * 流式执行测试共用：按生产姿态起一个带 sshd 的会话容器（去掉全部能力、加回业务服务的那几项和 SYS_CHROOT、
 * no-new-privileges、断网、启动时生成主机密钥），以及缺前提时的跳过原因。
 * 镜像默认 knowwork-sandbox:ssh-world，KNOWWORK_TEST_SSH_IMAGE 可改。
 */

import { execFileSync } from 'node:child_process'

export const SSH_IMAGE = process.env.KNOWWORK_TEST_SSH_IMAGE ?? 'knowwork-sandbox:ssh-world'
// 与业务服务 containers.CAPABILITIES + SSH_CAPABILITIES 一致
const CAPS = ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'FSETID', 'SETGID', 'SETUID', 'KILL', 'SYS_CHROOT']
// 与业务服务 scripts.ENTRYPOINT_SSH 一致
const ENTRY = 'if command -v ssh-keygen >/dev/null 2>&1 && [ ! -s /etc/ssh/ssh_host_ed25519_key ]; then '
  + 'ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1; fi; '
  + 'mkdir -p /workspace/input /workspace/tmp /workspace/output 2>/dev/null; '
  + 'mkdir -p "$(dirname "$KW_SESSION_ROOT")" && ln -sfn /workspace "$KW_SESSION_ROOT"; exec sleep infinity'

export const run = (cmd, args) => execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()

/** 缺 Docker、镜像、ssh 或 nc 时返回跳过原因。 */
export function missingSshPrerequisites() {
  try { run('docker', ['image', 'inspect', SSH_IMAGE]) } catch { return `没有镜像 ${SSH_IMAGE}（或 Docker 不可用）` }
  for (const tool of ['ssh', 'nc']) {
    try { run('sh', ['-c', `command -v ${tool}`]) } catch { return `没有 ${tool}` }
  }
  return undefined
}

export async function until(check, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await check()) return
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error(`等不到：${label}`)
}

/** 起容器并等主机密钥生成完；返回 { name, helperRunning, remove }。 */
export async function startSshContainer(sessionId) {
  const name = `kw-host-ssh-test-${process.pid}-${sessionId}`
  run('docker', ['run', '-d', '--init', '--network', 'none', '--user', '0', '--cap-drop', 'ALL',
    ...CAPS.flatMap(cap => ['--cap-add', cap]), '--security-opt', 'no-new-privileges',
    '-e', `KW_SESSION_ROOT=/sessions/${sessionId}`, '--name', name, SSH_IMAGE, '/bin/sh', '-c', ENTRY])
  await until(() => run('docker', ['exec', name, 'sh', '-c', 'test -s /etc/ssh/ssh_host_ed25519_key.pub && echo y']) === 'y',
    '容器生成主机密钥')
  return {
    name,
    helperRunning: () => /helper\.js/.test(run('docker', ['exec', name, 'ps', '-eo', 'args'])),
    remove: () => { try { run('docker', ['rm', '-f', name]) } catch { /* 已经没了 */ } },
  }
}
