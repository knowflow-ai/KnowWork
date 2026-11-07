/**
 * 多用户适配层（二）：属主账本——属主级资源（定时任务这类）、发起它们的对话归哪个空间的哪个人。
 *
 * 官方插件把这些资源存在宿主上，默认全归一个属主；适配层在创建时记一笔，之后按调用者过滤、核对（owned-service）。
 * 只存归属，不存资源本身；资源删了账本里留着一条也无妨（没人能再按 id 拿到它）。
 * 存在宿主私有目录的一个 JSON 文件里（同凭据文件，容器里挂 tmpfs 之外的持久目录由部署决定）：先写临时文件再改名，
 * 写坏了不会读到半个文件；进程内按调用顺序同步写，同一宿主只有一个进程。
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

/** @typedef {import('./caller.mjs').Owner} Owner */

function load(path) {
  try {
    const data = JSON.parse(readFileSync(path, 'utf8'))
    return typeof data === 'object' && data !== null && !Array.isArray(data) ? data : {}
  } catch (error) {
    if (error?.code === 'ENOENT') return {}
    throw new Error(`属主账本读不了：${path}`, { cause: error })
  }
}

/**
 * @param {string} path 账本文件
 * @returns {{ ownerOf(kind: string, id: string): Owner | undefined, record(entries: Array<[kind: string, id: string, owner: Owner]>): void }}
 *   record 一次写进几条（一次落盘）
 */
export function createLedger(path) {
  let data = load(path)
  const save = next => {
    mkdirSync(dirname(path), { recursive: true })
    const temp = `${path}.${process.pid}.tmp`
    writeFileSync(temp, JSON.stringify(next), { mode: 0o600 })
    renameSync(temp, path)
    data = next
  }
  return {
    ownerOf: (kind, id) => data[kind]?.[id],
    record: entries => save(entries.reduce((next, [kind, id, owner]) => ({
      ...next, [kind]: { ...next[kind], [id]: { workspaceId: owner.workspaceId, userId: owner.userId } },
    }), data)),
  }
}
