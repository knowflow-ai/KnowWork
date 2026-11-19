// 自带 Word 技能的 docx_redline.py：修订作者默认取宿主给的 KNOWWORK_REVISION_AUTHOR（桌面端是「登录的人（AI）」），
// 没给时是 KnowWork Agent，命令行 --author 优先。人和 AI 的修订靠作者分开，作者不能由模型随手起名。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const REDLINE = new URL('../plugins/dsh-host/skills/resources/office/docx_redline.py', import.meta.url).pathname

function hasPythonDocx() {
  return spawnSync('python3', ['-c', 'import docx'], { encoding: 'utf8' }).status === 0
}

const python = (args, env = {}) => {
  const base = { ...process.env }
  delete base.KNOWWORK_REVISION_AUTHOR
  return spawnSync('python3', args, { env: { ...base, ...env }, encoding: 'utf8' })
}

/** 一段文字的 .docx，改它的一个词，返回输出里的修订作者 */
function redlineAuthors(dir, name, env, extra = []) {
  const source = join(dir, `${name}-原文.docx`)
  const output = join(dir, `${name}-修订.docx`)
  const edits = join(dir, `${name}.json`)
  const made = python(['-c', 'import sys, docx; d = docx.Document(); d.add_paragraph("付款比例为 30%。"); d.save(sys.argv[1])', source])
  assert.equal(made.status, 0, made.stderr)
  writeFileSync(edits, JSON.stringify([{ find: '30%', replace: '20%' }]))
  const run = python([REDLINE, source, edits, output, ...extra], env)
  assert.equal(run.status, 0, run.stderr)
  const read = python(['-c', 'import re, sys, zipfile; x = zipfile.ZipFile(sys.argv[1]).read("word/document.xml").decode(); print("\\n".join(sorted(set(re.findall(r"w:author=\\"([^\\"]*)\\"", x)))))', output])
  assert.equal(read.status, 0, read.stderr)
  return read.stdout.trim().split('\n').filter(Boolean)
}

test('修订作者：宿主给的名字优先于默认，命令行再优先', { skip: !hasPythonDocx() }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'kw-redline-'))
  assert.deepEqual(redlineAuthors(dir, 'a', { KNOWWORK_REVISION_AUTHOR: '张三（AI）' }), ['张三（AI）'])
  assert.deepEqual(redlineAuthors(dir, 'b', {}), ['KnowWork Agent'])
  assert.deepEqual(redlineAuthors(dir, 'c', { KNOWWORK_REVISION_AUTHOR: '张三（AI）' }, ['--author', '李四']), ['李四'])
})
