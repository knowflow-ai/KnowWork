/**
 * 读文档技能的辅助脚本（skills/resources/office/extract_text.py）：PDF 有 pdftotext（云端沙箱）就用它，文字按页标上
 * 「[第 n 页]」（同会话附件），扫描件、图片按退出码 3 说清楚提取不出来；旧格式先经 soffice.py 转新格式再交给 markitdown。
 * pdftotext、Office 引擎都用替身；要 markitdown 的那条只在装了它的机器上跑（CI 的 python3 没有）。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { RESOURCE_ROOT } from '../plugins/dsh-host/src/bundled-skills.mjs'

const SCRIPT = join(RESOURCE_ROOT, 'office', 'extract_text.py')

/** 一个工作目录（原文件）和一个临时目录（结果）；pdftotext 的替身把 <原文件>.txt 原样打出来。 */
function scratch() {
  const dir = mkdtempSync(join(tmpdir(), 'kw-extract-'))
  const bin = join(dir, 'bin')
  mkdirSync(bin)
  mkdirSync(join(dir, 'tmp'))
  writeFileSync(join(bin, 'pdftotext'), `#!/bin/sh
echo "$@" >> "${join(dir, 'pdftotext.log')}"
for last; do :; done
[ "$last" = "-" ] || exit 9
for a; do case "$a" in *.pdf) cat "$a.txt"; exit $? ;; esac; done
`)
  chmodSync(join(bin, 'pdftotext'), 0o755)
  return { dir, env: { PATH: `${bin}:${process.env.PATH}` } }
}

const extract = ({ dir, env }, file) =>
  spawnSync('python3', [SCRIPT, file, 'tmp'], { cwd: dir, env: { ...process.env, ...env }, encoding: 'utf8' })

const line = n => `本季度第 ${n} 项指标同比增长，主要来自华东区域的渠道扩张和老客户续约。`

test('PDF：经 pdftotext 提取，每页标上「[第 n 页]」，连续空行压成一个，打印字数、页数和每页开头的目录', () => {
  const box = scratch()
  writeFileSync(join(box.dir, '季报.pdf'), '%PDF')
  const pages = [1, 2].map(p => `第${p}章 经营情况\n\n\n\n${line(1)}\n${line(2)}\n`)
  writeFileSync(join(box.dir, '季报.pdf.txt'), `${pages.join('\f')}\f`)
  const result = extract(box, '季报.pdf')
  assert.equal(result.status, 0, result.stderr)
  assert.match(readFileSync(join(box.dir, 'pdftotext.log'), 'utf8'), /^-layout -enc UTF-8 季报\.pdf -$/m)
  const text = readFileSync(join(box.dir, 'tmp', '季报.md'), 'utf8')
  assert.ok(text.startsWith('[第 1 页]\n第1章 经营情况\n\n本季度'), text)
  assert.match(text, /\n\n\[第 2 页\]\n第2章 经营情况/)
  assert.ok(!text.includes('\f') && !text.includes('\n\n\n'), '换页符或连续空行没处理')
  assert.match(result.stdout, /^已转成文字：tmp\/季报\.md（\d+ 字，共 2 页）/)
  assert.match(result.stdout, /\n {2}1: 第 1 页：第1章 经营情况\n {2}\d+: 第 2 页：第2章 经营情况/)
})

test('扫描件（每页几乎没有字）、图片：退出码 3，说清楚要识别文字，不留结果文件；同名的结果不互相覆盖', () => {
  const box = scratch()
  writeFileSync(join(box.dir, '扫描.pdf'), '%PDF')
  writeFileSync(join(box.dir, '扫描.pdf.txt'), '\f\f第3页\f')
  const scanned = extract(box, '扫描.pdf')
  assert.equal(scanned.status, 3)
  assert.match(scanned.stderr, /共 3 页.*扫描件/)
  writeFileSync(join(box.dir, '照片.JPG'), 'jpg')
  const image = extract(box, '照片.JPG')
  assert.equal(image.status, 3)
  assert.match(image.stderr, /图片/)
  assert.deepEqual(readdirSync(join(box.dir, 'tmp')), [])

  writeFileSync(join(box.dir, '季报.pdf'), '%PDF')
  writeFileSync(join(box.dir, '季报.pdf.txt'), `${line(1)}\n${line(2)}\f`)
  assert.equal(extract(box, '季报.pdf').status, 0)
  assert.equal(extract(box, '季报.pdf').status, 0)
  assert.deepEqual(readdirSync(join(box.dir, 'tmp')).sort(), ['季报-2.md', '季报.md'])
})

test('找不到文件、临时目录不存在、PDF 打不开：说清楚原因，退出码 2 或 4', () => {
  const box = scratch()
  const missing = extract(box, '没有.pdf')
  assert.equal(missing.status, 2)
  assert.match(missing.stderr, /找不到文件/)
  writeFileSync(join(box.dir, '坏.pdf'), '%PDF')
  const broken = extract(box, '坏.pdf')
  assert.equal(broken.status, 4)
  assert.match(broken.stderr, /PDF 打不开/)
  const noTmp = spawnSync('python3', [SCRIPT, '坏.pdf', 'nowhere'], { cwd: box.dir, env: { ...process.env, ...box.env }, encoding: 'utf8' })
  assert.equal(noTmp.status, 2)
  assert.match(noTmp.stderr, /临时目录不存在/)
})

function hasMarkitdown() {
  return spawnSync('python3', ['-c', 'import markitdown, docx'], { encoding: 'utf8' }).status === 0
}

test('旧格式：先经 Office 引擎转成新格式（放在临时目录里），再交给 markitdown', { skip: !hasMarkitdown() }, () => {
  const box = scratch()
  writeFileSync(join(box.dir, '合同.doc'), 'old word')
  // 原生 soffice 的替身：「转换」出一份真的 docx（用 python-docx 写），只看封装怎么调它
  const soffice = join(box.dir, 'bin', 'soffice')
  writeFileSync(soffice, `#!/bin/sh
out=""; for a; do [ "$prev" = "--outdir" ] && out="$a"; prev="$a"; done
python3 -c "import docx,sys; d=docx.Document(); d.add_heading('合同条款',1); d.add_paragraph('第一条 付款方式'); d.save(sys.argv[1])" "$out/合同.docx"
`)
  chmodSync(soffice, 0o755)
  const result = extract(box, '合同.doc')
  assert.equal(result.status, 0, result.stderr)
  assert.ok(existsSync(join(box.dir, 'tmp', '.converted', '合同.docx')))
  assert.match(readFileSync(join(box.dir, 'tmp', '合同.md'), 'utf8'), /# 合同条款[\s\S]*第一条 付款方式/)
})
