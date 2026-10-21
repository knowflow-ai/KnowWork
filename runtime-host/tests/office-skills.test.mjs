/**
 * 自带的 Office 技能（照搬 KnowFlow）：按目录发现，正文原样交给模型，资源目录是容器里的脚本目录，
 * 不把宿主机路径告诉模型；不再挂官方 dsh-skill-office。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { SKILL_ROOT, bundledSkillNames, parseSkill } from '../plugins/dsh-host/src/bundled-skills.mjs'

test('自带技能按目录发现：Word、Excel、PPT 三个，frontmatter 都能解析，脚本路径指向 KnowWork 镜像', () => {
  assert.deepEqual(bundledSkillNames(), ['office-docx', 'office-pptx', 'office-xlsx'])
  for (const name of bundledSkillNames()) {
    const raw = readFileSync(join(SKILL_ROOT, name, 'SKILL.md'), 'utf8')
    const { description, content } = parseSkill(raw, name)
    assert.ok(description.length > 0 && content.length > 0, name)
    assert.ok(!raw.includes('/opt/knowflow') && !raw.includes('read_file'), `${name} 还留着 KnowFlow 的路径或工具名`)
    assert.match(content, /present/, `${name} 没说怎么交付`)
  }
})

test('加载 Word 技能：正文原样交给模型，资源目录是容器里的脚本目录，没有宿主机路径；不再有官方 office 技能', async () => {
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
    const loaded = requests.at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content)).join('\n')
    const body = parseSkill(readFileSync(join(SKILL_ROOT, 'office-docx', 'SKILL.md'), 'utf8'), 'x').content
    assert.ok(loaded.includes(body.slice(0, 200)), '正文没有原样交给模型')
    assert.ok(loaded.includes('/opt/knowwork/office'), `资源目录不是容器路径：${loaded.slice(0, 300)}`)
    assert.ok(!loaded.includes(process.cwd()), '泄露了宿主机路径')
  } finally {
    await host.stop()
    await gateway.close()
  }
})
