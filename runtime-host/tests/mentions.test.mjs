import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mentionBlocks, mentionsOf, MENTION_PREFIX } from '../plugins/dsh-host/src/mentions.mjs'

test('@ 的 MCP 服务：写成「这一轮优先用它的工具」，带工具名前缀；不提知识检索的限定；标识格式不对拒收', () => {
  const mentions = mentionsOf([{ type: 'mcp', id: 'context7', name: 'Context7' }])
  const [block] = mentionBlocks(mentions)
  assert.ok(block.text.startsWith(MENTION_PREFIX + JSON.stringify(mentions)), '第一行是给业务服务投影用的 JSON')
  assert.match(block.text, /优先用这些外部服务：@Context7（工具名以 mcp__context7__ 开头）/)
  assert.match(block.text, /不代替查其他资料/, '点名不排斥别的资料和工具')
  assert.doesNotMatch(block.text, /knowledge_search/, '只点名了 MCP 时不提检索范围')
  for (const id of ['Context7', 'mcp__context7__x', '7up', 'a'.repeat(25), 'ent_', 'pub_gitlab']) {
    assert.throws(() => mentionsOf([{ type: 'mcp', id, name: 'x' }]), /MCP 服务标识/, id)
  }
  const [enterprise] = mentionBlocks(mentionsOf([{ type: 'mcp', id: 'ent_gitlab', name: 'GitLab' }]))
  assert.match(enterprise.text, /mcp__ent_gitlab__/, '企业 MCP 的标识带 ent_ 前缀')
})

test('知识库与 MCP 一起点名：两句都在', () => {
  const [block] = mentionBlocks(mentionsOf([
    { type: 'knowledge_base', id: 'builtin.kb1', name: '制度库' },
    { type: 'mcp', id: 'gitlab', name: 'GitLab' },
  ]))
  assert.match(block.text, /knowledge_search 已由系统限定/)
  assert.match(block.text, /mcp__gitlab__/)
})
