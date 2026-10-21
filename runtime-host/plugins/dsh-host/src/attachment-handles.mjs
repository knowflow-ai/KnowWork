/**
 * 本轮消息附带的文件：给模型的说明块。
 *
 * 照官方 dsh-attachment 的模型体验：非图片文件不把字节塞进请求，每个文件给一行确定的说明（文件名、字节数、
 * 摘要前缀、只读路径），模型需要时用文件工具读。说明文字逐字照搬 dsh-llm 的 fileHandleText（0.2.1-rc.2）。
 * 不同的只是存放位置：官方存在宿主目录里，靠执行环境的 processPathFromHostPath 映射；我们的执行环境是
 * 每个会话一个容器，不能把宿主目录挂进去（会破坏会话隔离），所以文件由业务服务放进本会话容器的
 * /workspace/input，这里只把路径写给模型。配了文档解析的，另外一行说明解析出的 Markdown。
 *
 * 每个文件单独一个文本块跟在用户原话后面：会话历史里持久保留（后面几轮模型仍知道有这些文件），
 * 业务服务投影时按固定格式把它们从显示文字里拿出来，换成附件卡片。
 */

export const MAX_ATTACHMENTS = 20
const INPUT_DIR = '/workspace/input/'
const MAX_NAME = 200

function invalid(message) {
  return Object.assign(new Error(message), { code: 'invalid_request' })
}

function inputPath(value, field) {
  if (typeof value !== 'string' || !value.startsWith(INPUT_DIR) || value.length > INPUT_DIR.length + MAX_NAME
      || value.includes('/../') || value.slice(INPUT_DIR.length).includes('/')) {
    throw invalid(`attachments.${field} 必须是 /workspace/input 下的文件`)
  }
  return value
}

/** 校验业务服务给的附件清单；不合法直接拒绝整条消息。 */
export function attachmentsOf(raw) {
  if (raw === undefined) return []
  if (!Array.isArray(raw) || raw.length > MAX_ATTACHMENTS) throw invalid(`attachments 最多 ${MAX_ATTACHMENTS} 个`)
  return raw.map((item) => {
    if (typeof item !== 'object' || item === null) throw invalid('attachments 格式错误')
    const { name, bytes, sha256 } = item
    if (typeof name !== 'string' || name.length === 0 || name.length > MAX_NAME) throw invalid('attachments.name 格式错误')
    if (!Number.isSafeInteger(bytes) || bytes < 0) throw invalid('attachments.bytes 格式错误')
    if (typeof sha256 !== 'string' || !/^[0-9a-f]{8,64}$/.test(sha256)) throw invalid('attachments.sha256 格式错误')
    const path = inputPath(item.path, 'path')
    const extractedPath = item.extractedPath === undefined ? undefined : inputPath(item.extractedPath, 'extractedPath')
    return { name, bytes, sha256, path, extractedPath }
  })
}

/** 官方 fileHandleText 的同一句话（quoted 即 JSON.stringify，摘要取 8 位）。 */
export function fileHandleText(file) {
  const identity = `File ${JSON.stringify(file.name)} (${file.bytes} bytes, sha256:${file.sha256.slice(0, 8)})`
  return `[${identity}: verbatim read-only copy saved at ${JSON.stringify(file.path)}. Read that path with your file tools when its contents are needed; copy it to a writable location before modifying it. When delegating file work, include this saved path in the delegation prompt; only subagents sharing this execution environment can read it.]`
}

/** 解析出的 Markdown：读长文档优先用它，原件仍是准绳。 */
export function extractedText(file) {
  return `[Markdown extracted from ${JSON.stringify(file.name)} by the document parser, saved at ${JSON.stringify(file.extractedPath)}. Prefer reading it for long documents; the original file stays the source of truth.]`
}

/** 用户原话之后，每个文件一个说明块（有解析结果的再跟一块）。 */
export function attachmentBlocks(files) {
  return files.flatMap((file) => [
    { type: 'text', text: fileHandleText(file) },
    ...(file.extractedPath === undefined ? [] : [{ type: 'text', text: extractedText(file) }]),
  ])
}
