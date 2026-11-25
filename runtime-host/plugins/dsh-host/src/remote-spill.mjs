/**
 * 服务端的溢出存储（官方 dsh-spill 的 SpillStore）：官方大输出溢出策略（dsh-spill-policy）把超出预算的工具结果完整存一份，
 * 给模型留开头和结尾、加一句「完整结果在某个路径」。官方的本机后端 dsh-spill-local 写在宿主机上，只适合「智能体和文件在
 * 同一台机器」的组合；服务端的模型只能读会话容器里的文件（remote-fs），所以换成这一份：完整内容写进会话工作区的
 * tmp/tool-results/ 下，返回模型在会话里看到的写法（/workspace/...），模型照官方提示用 read、grep 去读。
 *
 * - 存储按会话：子 Agent 的结果写进根会话的容器（同一个工作区，子 Agent 读得到）；
 * - 名字照官方：随机前缀加调用方建议名字的安全写法，不等于建议名字；
 * - 写不进去（会话已释放、沙箱不可用、超过单个文件的写入上限）就报错，官方策略按「尽力而为」保留原结果。
 */

import { randomBytes } from 'node:crypto'
import SpillStore, { SpillLocator } from '@deepseek-ai/dsh-spill'
import { createSandboxClient } from './sandbox-client.mjs'
import { rootSessionIdOf } from './state.mjs'
import { SANDBOX } from './workpath.mjs'

export const name = 'knowwork-remote-spill'

const FOLDER = 'tmp/tool-results'
const MAX_NAME = 80
// 同官方 dsh-spill-local 给模型的取回提示
const RETRIEVAL_HINT = 'Use read with offset/limit, or grep this path to search within it.'

/** 调用方建议的名字 → 一段安全的文件名（只留字母、数字、点、横线、下划线） */
export function safeName(suggested) {
  const cleaned = String(suggested ?? '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._]+/, '').slice(0, MAX_NAME)
  return cleaned || 'result.txt'
}

export default class RemoteSpillStore extends SpillStore {
  constructor(ctx, config = {}) {
    super(ctx)
    this.client = createSandboxClient({ baseUrl: config.baseUrl })
  }

  async saveText(input) {
    const sessionId = rootSessionIdOf(String(input.owner.sessionId))
    if (sessionId === undefined) throw new Error(`会话 ${input.owner.sessionId} 不在宿主上，溢出结果没地方存`)
    const relative = `${FOLDER}/${randomBytes(6).toString('hex')}-${safeName(input.suggestedName)}`
    const data = Buffer.from(input.content, 'utf8')
    await this.client.request('write', sessionId, { path: relative, data: data.toString('base64') })
    return { locator: SpillLocator(SANDBOX.at(relative)), bytes: data.byteLength, retrievalHint: RETRIEVAL_HINT }
  }
}
