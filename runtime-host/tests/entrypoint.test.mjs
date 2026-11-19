// 运行时宿主容器入口（deploy/runtime-host/entrypoint.sh）装配配置档：开源那几层照模板；扩展的镜像（例如带在线编辑插件的
// 运行时宿主）经 KNOWWORK_HOST_BUNDLES 多挂几层，包装在 $APP_DIR/node_modules 下，排在开源那几层之后（同 scripts/dev_stack.py）。
// 真的跑入口脚本，dsh 换成一个只把装好的配置档报出来的假命令。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const ENTRYPOINT = join(ROOT, 'deploy', 'runtime-host', 'entrypoint.sh')

function app() {
  const base = mkdtempSync(join(tmpdir(), 'kw-entrypoint-'))
  const dir = join(base, 'app')
  mkdirSync(join(dir, 'profile'), { recursive: true })
  for (const name of ['package.json', 'cordis.yml', 'cordis.patch.yml', 'pnpm-workspace.yaml']) {
    copyFileSync(join(ROOT, 'runtime-host', 'profile', name), join(dir, 'profile', name))
  }
  mkdirSync(join(dir, 'plugins', 'dsh-host'), { recursive: true })
  mkdirSync(join(dir, 'plugins', 'host-bundle'), { recursive: true })
  mkdirSync(join(dir, 'node_modules', '@knowwork', 'office-host'), { recursive: true })
  writeFileSync(join(dir, 'node_modules', '@knowwork', 'office-host', 'package.json'), '{"name":"@knowwork/office-host"}')
  // 假的 dsh：把装好的配置档报出来就退出
  mkdirSync(join(dir, 'node_modules', '.bin'), { recursive: true })
  const dsh = join(dir, 'node_modules', '.bin', 'dsh')
  writeFileSync(dsh, '#!/bin/sh\ncat "$DSH_HOME/profiles/knowwork-host/package.json"\n')
  chmodSync(dsh, 0o755)
  const token = join(base, 'host_token')
  writeFileSync(token, 'h'.repeat(40))
  return { dir, home: join(base, 'dsh'), token }
}

function start(where, extra = {}) {
  const out = execFileSync('sh', [ENTRYPOINT], {
    env: { PATH: process.env.PATH, KNOWWORK_APP_DIR: where.dir, DSH_HOME: where.home,
      KNOWWORK_HOST_TOKEN_FILE: where.token, ...extra },
    encoding: 'utf8',
  })
  return JSON.parse(out).dsh.profile.bundles
}

const template = () => JSON.parse(readFileSync(join(ROOT, 'runtime-host', 'profile', 'package.json'), 'utf8')).dsh.profile.bundles

test('the open layers come from the template unchanged', () => {
  assert.deepEqual(start(app()), template())
})

test('an extension image adds its own layers after the open ones', () => {
  const where = app()
  const bundles = start(where, { KNOWWORK_HOST_BUNDLES: ' @knowwork/office-host ' })
  assert.deepEqual(bundles, [...template(), '@knowwork/office-host'])
  const linked = join(where.home, 'profiles', 'knowwork-host', 'node_modules', '@knowwork', 'office-host')
  assert.equal(realpathSync(linked), realpathSync(join(where.dir, 'node_modules', '@knowwork', 'office-host')))
})

test('a layer that is not installed stops the host with a reason', () => {
  assert.throws(() => start(app(), { KNOWWORK_HOST_BUNDLES: '@knowwork/missing' }), /missing/)
})
