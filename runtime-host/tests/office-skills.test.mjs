/**
 * 自带的 Office 技能（照搬 KnowFlow）：按目录发现，正文原样交给模型；脚本照官方技能包写成相对资源目录的路径，
 * 服务端宿主上资源目录是沙箱镜像里的 /opt/knowwork（环境层配的），不把宿主机路径告诉模型；不再挂官方 dsh-skill-office。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { RESOURCE_ROOT, SKILL_ROOT, bundledSkillNames, parseSkill } from '../plugins/dsh-host/src/bundled-skills.mjs'

test('自带技能按目录发现：Word、Excel、PPT 三个、画示意图的 diagram 和读文档的 read-document，frontmatter 都能解析，引用的脚本都在资源目录里', () => {
  assert.deepEqual(bundledSkillNames(), ['diagram', 'office-docx', 'office-pptx', 'office-xlsx', 'read-document'])
  for (const name of bundledSkillNames()) {
    const raw = readFileSync(join(SKILL_ROOT, name, 'SKILL.md'), 'utf8')
    const { description, content } = parseSkill(raw, name)
    assert.ok(description.length > 0 && content.length > 0, name)
    assert.ok(!raw.includes('/opt/knowflow') && !raw.includes('read_file'), `${name} 还留着 KnowFlow 的路径或工具名`)
    if (name.startsWith('office-')) assert.match(content, /present/, `${name} 没说怎么交付`)
    // 两种执行环境都能用：不写死容器里的路径，脚本按资源目录、数据按工作目录
    assert.ok(!raw.includes('/opt/knowwork') && !raw.includes('/workspace'), `${name} 还写死着容器里的路径`)
  }
  const scripts = readdirSync(join(RESOURCE_ROOT, 'office'))
  for (const name of bundledSkillNames().filter(n => n.startsWith('office-') || n === 'read-document')) {
    const raw = readFileSync(join(SKILL_ROOT, name, 'SKILL.md'), 'utf8')
    for (const [, script] of raw.matchAll(/office\/([a-z_]+\.py)/g)) assert.ok(scripts.includes(script), `${name} 引用的 ${script} 不在资源目录里`)
  }
})

test('加载 Word 技能：正文原样交给模型，资源目录是沙箱镜像里的 /opt/knowwork，没有宿主机路径；不再有官方 office 技能', async () => {
  const gateway = await startOpenAiStub({ script: [{ tool: 'skill', args: { name: 'office-docx' } }, { text: '好' }] })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-office-0001-aaaaaaa',
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'of-1' })
    await host.call('POST', '/v1/sessions/of-1/messages', { text: '把这份合同按修订模式改一下' })
    await collectEvents(host, 'of-1', isTurnEnd)
    const requests = gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
    const catalog = JSON.stringify(requests[0].body.messages)
    assert.ok(catalog.includes('office-docx') && !catalog.includes('office-export-pdf'), '目录里还有官方 Office 技能')
    assert.ok(catalog.includes('diagram'), '画示意图的自带技能不在模型看到的技能目录里')
    const loaded = requests.at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content)).join('\n')
    const body = parseSkill(readFileSync(join(SKILL_ROOT, 'office-docx', 'SKILL.md'), 'utf8'), 'x').content
    assert.ok(loaded.includes(body.slice(0, 200)), '正文没有原样交给模型')
    assert.ok(loaded.includes('Base directory for this skill: /opt/knowwork\n'), `资源目录不是容器路径：${loaded.slice(0, 300)}`)
    assert.ok(!loaded.includes(process.cwd()), '泄露了宿主机路径')
  } finally {
    await host.stop()
    await gateway.close()
  }
})

test('业务服务能问到自带技能清单（技能页与 @ 菜单列出来用）', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    const { status, body: { skills } } = await host.call('GET', '/v1/skills/bundled')
    assert.equal(status, 200)
    assert.deepEqual(skills.map(s => s.name), ['diagram', 'office-docx', 'office-pptx', 'office-xlsx', 'read-document'])
    assert.ok(skills.every(s => s.description.length > 0 && s.userInvocable === true))
  } finally {
    await host.stop()
  }
})
