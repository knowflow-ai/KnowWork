import { test } from 'node:test'
import assert from 'node:assert/strict'
import { officialPackages, preflight, releaseProblem } from '../scripts/preflight-dsh-upgrade.mjs'

const fullDigest = `sha512-${Buffer.alloc(64, 3).toString('base64')}`
const release = (name, version, extra = {}) => ({
  name, version, dist: { tarball: `https://registry.npmjs.org/${name}/-/x-${version}.tgz`, integrity: fullDigest, ...extra },
})

test('要查的包：锁文件里随 DSH 同版本的全部官方包，加上配置层按名字挂的插件', async () => {
  const { current, names } = await officialPackages()
  assert.equal(current, '0.2.1-rc.2')
  for (const name of ['@deepseek-ai/dsh', '@deepseek-ai/dsh-user-approval', '@deepseek-ai/dsh-llm-pi-ai',
    '@deepseek-ai/dsh-sdk-minimal']) assert.ok(names.includes(name), `缺 ${name}`)
  // 有自己版本号的包不按候选版本查
  assert.ok(!names.includes('@deepseek-ai/cordis') && !names.includes('@deepseek-ai/schemastery'))
})

test('发布信息：只认官方仓库地址与完整的 SHA-512 摘要', () => {
  const name = '@deepseek-ai/dsh'
  assert.equal(releaseProblem(release(name, '1.0.0'), name, '1.0.0'), null)
  assert.match(releaseProblem(release(name, '1.0.1'), name, '1.0.0'), /对不上/)
  assert.match(releaseProblem(release(name, '1.0.0', { tarball: 'https://mirror.example/x.tgz' }), name, '1.0.0'), /官方仓库/)
  assert.match(releaseProblem(release(name, '1.0.0', { integrity: 'sha1-abc' }), name, '1.0.0'), /SHA-512/)
  assert.match(releaseProblem(release(name, '1.0.0', { tarball: undefined }), name, '1.0.0'), /下载地址/)
})

test('有包没发布、或者发布信息不对，预检就不通过（假的仓库，不联网）', async () => {
  const requested = []
  const fetchJson = async url => {
    const [, encoded, version] = url.match(/^https:\/\/registry\.npmjs\.org\/([^/]+)\/(.+)$/)
    const name = decodeURIComponent(encoded)
    requested.push(name)
    if (name === '@deepseek-ai/dsh-user-approval') return { status: 404, body: null }
    if (name === '@deepseek-ai/dsh-llm-pi-ai') return { status: 200, body: release(name, version, { integrity: 'sha1-x' }) }
    return { status: 200, body: release(name, version) }
  }
  const report = await preflight('9.9.9', { fetchJson })
  assert.equal(report.ok, false)
  assert.deepEqual(report.missing, ['@deepseek-ai/dsh-user-approval'])
  assert.deepEqual(report.problems.map(p => p.name), ['@deepseek-ai/dsh-llm-pi-ai'])
  assert.equal(new Set(requested).size, requested.length, '同一个包只查一次')
  await assert.rejects(preflight('latest', { fetchJson }), /完整的版本号/)
})

test('全部发布时预检通过', async () => {
  const fetchJson = async url => {
    const [, encoded, version] = url.match(/^https:\/\/registry\.npmjs\.org\/([^/]+)\/(.+)$/)
    return { status: 200, body: release(decodeURIComponent(encoded), version) }
  }
  const report = await preflight('9.9.9', { fetchJson })
  assert.equal(report.ok, true)
  assert.equal(report.missing.length + report.problems.length, 0)
})
