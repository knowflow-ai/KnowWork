/**
 * 自带 Office 技能的引擎封装（skills/resources/office/soffice.py）：一个入口、两种引擎。
 * 云端沙箱里用原生 LibreOffice，参数原样交给 soffice（业务服务的格式转换也这样调，行为不能变）；用户电脑上的本机会话用
 * DSH 官方 LibreOffice Kit（桌面端经官方 shell 环境给出 DSH_KNOWWORK_OFFICE_NODE、DSH_KNOWWORK_OFFICE_CLI），
 * 技能用到的写法映射成它的子命令。两种引擎都用替身，只看封装怎么调它们。真引擎的本机路径在桌面端的 local-office 测试里。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { RESOURCE_ROOT } from '../plugins/dsh-host/src/bundled-skills.mjs'

const OFFICE = join(RESOURCE_ROOT, 'office')

function scratch() {
  const dir = mkdtempSync(join(tmpdir(), 'kw-office-'))
  writeFileSync(join(dir, 'a.docx'), 'not really a docx')
  return dir
}

/** 原生 LibreOffice 的替身：记下参数，按 --convert-to、--outdir 写出文件。 */
function fakeSoffice(dir) {
  const bin = join(dir, 'bin')
  mkdirSync(bin)
  const script = join(bin, 'soffice')
  writeFileSync(script, `#!/bin/sh
echo "$@" >> "${join(dir, 'soffice.log')}"
fmt=""; out="."; last=""
while [ $# -gt 0 ]; do
  case "$1" in --convert-to) fmt="$2"; shift ;; --outdir) out="$2"; shift ;; *) last="$1" ;; esac
  shift
done
base=$(basename "$last"); mkdir -p "$out"; echo converted > "$out/\${base%.*}.\${fmt%%:*}"
`)
  chmodSync(script, 0o755)
  // poppler 的替身：-f/-l 那一页出一张 <前缀>-<页码>.png
  const pdftoppm = join(bin, 'pdftoppm')
  writeFileSync(pdftoppm, `#!/bin/sh
echo "$@" >> "${join(dir, 'pdftoppm.log')}"
page=1; prefix=""
while [ $# -gt 0 ]; do case "$1" in -f) page="$2"; shift ;; *) prefix="$1" ;; esac; shift; done
echo png > "$prefix-$page.png"
`)
  chmodSync(pdftoppm, 0o755)
  return { PATH: `${bin}:${process.env.PATH}` }
}

/** 官方 LibreOffice Kit 命令行的替身：记下子命令，照官方的输出写文件与 manifest.json。 */
function fakeKit(dir) {
  const cli = join(dir, 'cli.js')
  writeFileSync(cli, `
const fs = require('node:fs'); const path = require('node:path')
const [command, ...rest] = process.argv.slice(2)
fs.appendFileSync(${JSON.stringify(join(dir, 'kit.log'))}, JSON.stringify([command, ...rest]) + '\\n')
const arg = name => rest[rest.indexOf(name) + 1]
if (command === 'convert' && arg('--output').endsWith('.pptx')) { console.log(JSON.stringify({ code: 'failed', error: 'Unsupported conversion: docx → pptx.' })); process.exit(1) }
if (command === 'convert' || command === 'recalculate') fs.writeFileSync(arg('--output'), 'done')
if (command === 'render') {
  const out = arg('--output-dir'); fs.mkdirSync(out)
  const images = [1, 2].map(index => { const file = path.join(out, 'page-000' + index + '.png'); fs.writeFileSync(file, 'png'); return { index, page: index, path: file } })
  fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({ images: images.reverse() }))
}
console.log(JSON.stringify({ backend: 'native' }))
`)
  return { DSH_KNOWWORK_OFFICE_NODE: process.execPath, DSH_KNOWWORK_OFFICE_CLI: cli }
}

const python = (dir, env, ...args) => spawnSync('python3', args, { cwd: dir, env: { ...process.env, ...env }, encoding: 'utf8' })

test('云端沙箱：参数原样交给原生 soffice，每次带独立的配置目录', () => {
  const dir = scratch()
  const result = python(dir, fakeSoffice(dir), join(OFFICE, 'soffice.py'), '--headless', '--convert-to', 'pdf', '--outdir', 'out', 'a.docx')
  assert.equal(result.status, 0, result.stderr)
  const logged = readFileSync(join(dir, 'soffice.log'), 'utf8').trim()
  assert.match(logged, /^-env:UserInstallation=file:\/\/\S+lo-profile-\S+ --headless --convert-to pdf --outdir out a\.docx$/)
  assert.ok(existsSync(join(dir, 'out/a.pdf')))
})

test('本机：技能里的写法映射成官方 LibreOffice Kit 的子命令，带上桌面端给的字体回退；不认的参数说清楚', () => {
  const dir = scratch()
  const fonts = JSON.stringify([['微软雅黑', 'Hiragino Sans GB']])
  const env = { ...fakeKit(dir), DSH_KNOWWORK_OFFICE_FONT_FALLBACKS: fonts }
  const converted = python(dir, env, join(OFFICE, 'soffice.py'), '--headless', '--convert-to', 'pdf', '--outdir', 'out', 'a.docx')
  assert.equal(converted.status, 0, converted.stderr)
  assert.equal(converted.stdout.trim(), join('out', 'a.pdf'))
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'kit.log'), 'utf8').trim()),
    ['convert', '--input', 'a.docx', '--output', join('out', 'a.pdf'), '--font-fallbacks', fonts])
  const unsupported = python(dir, env, join(OFFICE, 'soffice.py'), '--convert-to', 'pdf', '--infilter=x', 'a.docx')
  assert.equal(unsupported.status, 2)
  assert.match(unsupported.stderr, /不认 --infilter=x/)
  const failed = python(dir, env, join(OFFICE, 'soffice.py'), '--convert-to', 'pptx', 'a.docx')
  assert.equal(failed.status, 1)
  assert.match(failed.stderr, /Unsupported conversion/, '官方 Kit 的错误原样给出')
})

test('页面图：本机按官方 manifest 的页序给出路径；云端走 PDF 再 pdftoppm', () => {
  const dir = scratch()
  const rendered = python(dir, fakeKit(dir), join(OFFICE, 'render.py'), 'a.docx', 'pages', '--pages', '2,1')
  assert.equal(rendered.status, 0, rendered.stderr)
  const real = realpathSync(dir)
  assert.deepEqual(rendered.stdout.trim().split('\n'), [join(real, 'pages/page-0001.png'), join(real, 'pages/page-0002.png')])
  const call = readFileSync(join(dir, 'kit.log'), 'utf8').trim().split('\n').map(line => JSON.parse(line)).at(-1)
  assert.deepEqual(call, ['render', '--input', 'a.docx', '--output-dir', 'pages', '--pages', '1,2'])
  assert.equal(python(dir, fakeKit(dir), join(OFFICE, 'render.py'), 'a.docx', 'pages').status, 2, '输出目录已经有东西就不渲')

  const cloud = scratch()
  const native = python(cloud, fakeSoffice(cloud), join(OFFICE, 'render.py'), 'a.docx', 'pages', '--pages', '3')
  assert.equal(native.status, 0, native.stderr)
  assert.deepEqual(native.stdout.trim().split('\n'), [join(realpathSync(cloud), 'pages/page-3.png')])
  assert.match(readFileSync(join(cloud, 'soffice.log'), 'utf8'), /--convert-to pdf/, '先转 PDF')
  assert.match(readFileSync(join(cloud, 'pdftoppm.log'), 'utf8'), /^-png -r 100 -f 3 -l 3 /, '再按页出图')
})

test('公式重算：本机交给官方 Kit 的 recalculate，再照样核对错误值', { skip: !hasOpenpyxl() }, () => {
  const dir = scratch()
  execFileSync('python3', ['-c', "from openpyxl import Workbook\nwb=Workbook(); wb.active['A1']='=1+1'; wb.save('b.xlsx')"], { cwd: dir })
  const env = fakeKit(dir)
  // 替身不真算：把输入原样拷成输出，重算脚本照样读出公式、核对错误值
  writeFileSync(env.DSH_KNOWWORK_OFFICE_CLI, readFileSync(env.DSH_KNOWWORK_OFFICE_CLI, 'utf8').replace(
    "if (command === 'convert' || command === 'recalculate') fs.writeFileSync(arg('--output'), 'done')",
    "if (command === 'recalculate') fs.copyFileSync(arg('--input'), arg('--output'))"))
  const result = python(dir, env, join(OFFICE, 'recalc.py'), 'b.xlsx')
  assert.equal(result.status, 0, result.stderr)
  assert.equal(JSON.parse(result.stdout).total_formulas, 1)
  assert.equal(JSON.parse(readFileSync(join(dir, 'kit.log'), 'utf8').trim())[0], 'recalculate')
})

function hasOpenpyxl() {
  return spawnSync('python3', ['-c', 'import openpyxl'], { encoding: 'utf8' }).status === 0
}
