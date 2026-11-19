/**
 * 根目录的 zod 要和 DSH 包钉的是同一版。DSH 的包都钉死一个 zod 版本（0.2.2-rc.5 是 4.6.6），别的依赖只写范围（如 ^4.2.0）。
 * npm 按范围在根目录装最新的（4.6.7），钉死旧版的七十多个 DSH 包就各装一份自己的 zod（每份 8MB），宿主镜像多出五百多 MB
 * （2026-11-19 升 rc.5 时实测官方包 632MB → 1.1GB，改后 536MB）。所以 package.json 里直接依赖 DSH 钉的那一版，让它占住根目录。
 * DSH 换了 zod 版本这条就失败，照着改 package.json。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const pinnedByDsh = require('@deepseek-ai/dsh-typert-protocol/package.json').dependencies.zod

test('根目录的 zod 是 DSH 包钉的那一版，DSH 的包不再各装一份', () => {
  const declared = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).dependencies.zod
  assert.equal(declared, pinnedByDsh, `package.json 里的 zod 要改成 DSH 钉的 ${pinnedByDsh}`)
  assert.equal(require('zod/package.json').version, pinnedByDsh, '根目录装的 zod 不是 DSH 钉的那一版：npm install 后再看')
})
