// Office 容器入口写 GenOffice 设置（deploy/docker/office/settings.sh）：新容器直接用 shell 写，不为一个 JSON 文件起一次
// Electron（冷启动多 0.5～1 秒）；停了又拉起的容器里 GenOffice 可能写过别的设置，照旧用它自带的 Node 合并。
// 真的跑脚本；合并那条路用 node 顶替 GenOffice（ELECTRON_RUN_AS_NODE 下两者一样跑 -e）。
// 跑法：node --test deploy/docker/office/tests/settings.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '..', 'settings.sh')

function settingsFile() {
  return join(mkdtempSync(join(tmpdir(), 'kw-office-settings-')), 'GenOffice', 'app-settings.json')
}

function write(file, author, port, genoffice) {
  execFileSync('sh', [SCRIPT, file, author, port, genoffice], { env: { PATH: process.env.PATH }, stdio: 'pipe' })
  return JSON.parse(readFileSync(file, 'utf8'))
}

test('a new container gets its settings without starting GenOffice', () => {
  // GenOffice 换成一跑就失败的命令：新容器这条路不该起它
  const written = write(settingsFile(), '张三 "老\\张"\t', '3093', '/bin/false')
  assert.deepEqual(written, {
    mcpEnabled: true,
    mcpPort: 3093,
    aiPanelShowSidebar: false,
    authorName: '张三 "老\\张"',
  })
})

test('no author name leaves the author to GenOffice', () => {
  assert.deepEqual(write(settingsFile(), '', '3100', '/bin/false'), {
    mcpEnabled: true,
    mcpPort: 3100,
    aiPanelShowSidebar: false,
  })
})

test('a restarted container keeps what GenOffice wrote and gets ours merged in', () => {
  const file = settingsFile()
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify({ zoom: 120, authorName: '旧名', mcpPort: 1 }))
  assert.deepEqual(write(file, '张三', '3093', process.execPath), {
    zoom: 120,
    authorName: '张三',
    mcpPort: 3093,
    mcpEnabled: true,
    aiPanelShowSidebar: false,
  })
})

test('a port that is not a number stops the container with a reason', () => {
  assert.throws(() => write(settingsFile(), '张三', '30x3', '/bin/false'), /端口/)
})
