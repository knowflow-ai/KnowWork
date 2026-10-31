/**
 * 技能清单与内容客户端的离线单测：业务服务的返回当作不可信输入校验，正文按摘要校验。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SkillCatalogError, bundledOffOf, catalogOf, createSkillClient, ensureCatalog, lockedTools } from '../plugins/dsh-host/src/skill-catalog.mjs'
import { publishedSkill, startSkillsStub } from './support/skills-stub.mjs'

const TOKEN = 'skills-token-unit-'.padEnd(40, 'u')
const extract = publishedSkill('contract-extract', { unlocks: ['extract_records'], completionTools: ['extract_records'] })
const notes = publishedSkill('meeting-notes')

test('合格的清单原样通过，并给出被认领工具到技能的对应', () => {
  const catalog = catalogOf({ skills: [extract.entry, notes.entry] })
  assert.deepEqual(catalog.map(s => s.name), ['contract-extract', 'meeting-notes'])
  assert.ok(Object.isFrozen(catalog[0]))
  assert.deepEqual([...lockedTools(catalog)], [['extract_records', 'contract-extract']])
})

test('不合格的清单一律拒绝', () => {
  const variants = [
    {},
    { skills: 'x' },
    { skills: [{ ...notes.entry, name: 'Bad_Name' }] },
    { skills: [{ ...notes.entry, description: '' }] },
    { skills: [{ ...notes.entry, version: '' }] },
    { skills: [{ ...notes.entry, digest: 'short' }] },
    { skills: [{ ...notes.entry, contentDigest: undefined }] },
    { skills: [{ ...notes.entry, allowedTools: ['1bad'] }] },
    { skills: [{ ...notes.entry, unlocks: 'x' }] },
    { skills: [{ ...notes.entry, unlocks: ['extract_records'], completionTools: [] }] },
    { skills: [notes.entry, notes.entry] },
    { skills: [extract.entry, { ...publishedSkill('other', { unlocks: ['extract_records'], completionTools: ['extract_records'] }).entry }] },
    { skills: Array.from({ length: 65 }, (_, i) => publishedSkill(`s-${i}`).entry) },
  ]
  for (const body of variants) assert.throws(() => catalogOf(body), SkillCatalogError, JSON.stringify(body).slice(0, 80))
})

test('内容客户端：带令牌与摘要拉取，摘要不符或业务服务拒绝时报错', async () => {
  const stub = await startSkillsStub({ skillsByToken: { [TOKEN]: [extract, notes] } })
  const tampered = await startSkillsStub({ skillsByToken: { [TOKEN]: [notes] }, tamper: true })
  try {
    const client = createSkillClient({ baseUrl: stub.baseUrl })
    const { skills: catalog, bundledOff } = await client.catalog(TOKEN)
    assert.deepEqual(bundledOff, [])
    assert.equal(await client.content(TOKEN, catalog[1]), notes.body)
    assert.deepEqual(stub.contentLoads()[0].query, { name: 'meeting-notes', digest: notes.entry.digest })
    await assert.rejects(client.catalog('wrong'.padEnd(40, 'w')), /令牌无效/)
    await assert.rejects(client.content(TOKEN, { ...catalog[1], digest: '0'.repeat(64) }), /不在本会话的可用范围内/)
    await assert.rejects(createSkillClient({ baseUrl: tampered.baseUrl }).content(TOKEN, catalog[1]), /校验失败/)
    await assert.rejects(createSkillClient({ baseUrl: 'http://127.0.0.1:1' }).catalog(TOKEN), /不可达/)
  } finally {
    await stub.close()
    await tampered.close()
  }
})

test('没有技能令牌的会话清单为空，不发请求', async () => {
  assert.deepEqual(await ensureCatalog({ id: 's', skillAccess: undefined }), [])
})

test('停用的自带技能：只认技能名，缺了当作没停用', () => {
  assert.deepEqual(bundledOffOf({}), [])
  assert.deepEqual(bundledOffOf({ bundledOff: ['office-pptx', 'office-pptx'] }), ['office-pptx'])
  for (const bundledOff of ['office-pptx', ['../x'], [1]]) {
    assert.throws(() => bundledOffOf({ bundledOff }), SkillCatalogError)
  }
})
