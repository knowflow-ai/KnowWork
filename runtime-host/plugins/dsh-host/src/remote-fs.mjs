/**
 * 远程 `ctx.fs`：官方文件工具、present、工作目录服务读写的是会话容器里的文件，不是宿主机的。
 *
 * 照官方 FileSystem 合同实现（行为对照 dsh-fs-local）：
 * - 只认 `/sessions/<会话 id>` 下的路径；相对路径按调用方给的 cwd（会话工作目录）解析；`/workspace/...` 换到 cwd 所在的
 *   会话下（容器里两者是同一个目录）；别处一律拒绝；
 * - targetKey 即规范化后的路径（不做 realpath：容器里工作区内的符号链接由容器自己解析）；
 * - 版本号取容器里的修改时间（纳秒）、inode 与字节数；写入与编辑按目标串行，读-判-写之间不交错；
 * - 每次操作都是「宿主 → 业务服务 → docker exec」一个来回，能省则省：沙箱的 read 顺带返回 stat，读文件不先 stat，
 *   编辑用读回来的版本核对（读 + 写两次）；写入要先知道文件在不在、版本对不对（大文件不能为拿版本整个读一遍），
 *   probe 之后读旧内容不再 stat；
 * - 文本读取与字面编辑的语义见 fs-text.mjs；不支持监听（基类默认拒绝）。
 */

import { FileSystem, FsError, FsTargetKey, FsVersion } from '@deepseek-ai/dsh-fs'
import { posix } from 'node:path'
import { applyLiteralEdit, decodeText, normalizeLineEndings } from './fs-text.mjs'
import { SandboxCallError, createSandboxClient, sessionPath, sessionRoot } from './sandbox-client.mjs'

const READ_CHUNK = 4 * 1024 * 1024
const MAX_TEXT_BYTES = 32 * 1024 * 1024
const DIFF_BASIS_MAX_BYTES = 10 * 1024 * 1024
const WORKSPACE = '/workspace'

const TYPES = { f: 'file', d: 'directory' }
const CODES = {
  not_found: 'FS_NOT_FOUND', is_directory: 'FS_NOT_REGULAR_FILE', not_regular_file: 'FS_NOT_REGULAR_FILE',
  permission_denied: 'FS_PERMISSION_DENIED', forbidden: 'FS_PERMISSION_DENIED', too_large: 'FS_TOO_LARGE',
}

function fsError(error, verb, displayPath) {
  if (error instanceof FsError) return error
  if (error?.name === 'AbortError' || error?.name === 'TimeoutError') return new FsError(`${verb} aborted`, 'FS_ABORTED', { cause: error })
  if (error instanceof SandboxCallError) {
    return new FsError(`cannot ${verb} "${displayPath}": ${error.message}`, CODES[error.code] ?? 'FS_IO_ERROR', { cause: error })
  }
  return new FsError(`cannot ${verb} "${displayPath}"`, 'FS_IO_ERROR', { cause: error })
}

function throwIfAborted(signal, verb) {
  if (signal?.aborted) throw new FsError(`${verb} aborted`, 'FS_ABORTED')
}

/**
 * 容器里会话工作区就是 /workspace（/sessions/<会话 id> 是指向它的链接），模型与技能习惯写 /workspace/...。
 * 调用方带了会话工作目录（cwd）时，把 /workspace 开头的路径换到这个会话下；没有 cwd 就认不出会话，原样返回（随后被拒）。
 */
function workspaceAlias(path, cwd) {
  const normal = posix.normalize(path)
  if (normal !== WORKSPACE && !normal.startsWith(`${WORKSPACE}/`)) return path
  const session = cwd === undefined ? undefined : sessionPath(cwd)
  return session === undefined ? path : sessionRoot(session.sessionId) + normal.slice(WORKSPACE.length)
}

export default class RemoteFileSystem extends FileSystem {
  locks = new Map()

  constructor(ctx, config = {}) {
    super(ctx)
    this.client = createSandboxClient({ baseUrl: config.baseUrl })
  }

  /** 同一目标的变更排队执行（照 dsh-fs-local 的 withLock）。 */
  async withLock(key, op) {
    const run = (this.locks.get(key) ?? Promise.resolve()).then(op, op)
    const tail = run.then(() => undefined, () => undefined)
    this.locks.set(key, tail)
    try {
      return await run
    } finally {
      if (this.locks.get(key) === tail) this.locks.delete(key)
    }
  }

  locate(path, cwd) {
    if (typeof path !== 'string' || path.trim().length === 0) throw new FsError('file_path must be a non-empty string', 'FS_NOT_FOUND')
    const absolute = path.startsWith('/') ? workspaceAlias(path, cwd) : cwd === undefined ? undefined : posix.join(cwd, path)
    const located = absolute === undefined ? undefined : sessionPath(absolute)
    // 调用方（模型的文件工具带着本会话的工作目录）只能落到自己的会话：沙箱请求用的是路径里那个会话的令牌，
    // 不核对的话，知道别人对话 id 的模型就能读写别人的容器。子 Agent 继承根会话的工作目录，照样通过
    const caller = cwd === undefined ? undefined : sessionPath(cwd)
    if (located === undefined || (cwd !== undefined && caller?.sessionId !== located.sessionId)) {
      throw new FsError(`cannot access "${path}": only files under the session working directory are available`, 'FS_PERMISSION_DENIED')
    }
    return located
  }

  async call(operation, target, body, verb, signal) {
    const { sessionId } = this.locate(String(target.targetKey))
    try {
      return await this.client.request(operation, sessionId, { path: String(target.targetKey), ...body }, { signal })
    } catch (error) {
      throw fsError(error, verb, target.displayPath)
    }
  }

  async probe(target, verb, signal, follow = true) {
    // 会话工作目录本身总是一个目录：工作目录服务开会话就要 stat 它，不为此去建容器，也不要求会话已拿到令牌
    const { path, sessionId } = this.locate(String(target.targetKey))
    if (path === sessionRoot(sessionId)) {
      return { kind: 'd', size: 0, version: 'session-root' }
    }
    const { stat } = await this.call('stat', target, { follow }, verb, signal)
    return stat ?? undefined
  }

  async resolve(path, opts) {
    throwIfAborted(opts?.signal, 'resolve')
    const { path: normal } = this.locate(path, opts?.cwd)
    return { targetKey: FsTargetKey(normal), displayPath: normal }
  }

  processPath(target) {
    return String(target.targetKey)
  }

  fileUrl(target) {
    return new URL(`file://${encodeURI(this.processPath(target))}`).href
  }

  contains(parent, child) {
    const base = this.processPath(parent)
    const path = this.processPath(child)
    return path === base || path.startsWith(`${base}/`)
  }

  async stat(target, signal) {
    throwIfAborted(signal, 'stat')
    const info = await this.probe(target, 'stat', signal)
    if (info === undefined) return undefined
    return { version: FsVersion(info.version), type: TYPES[info.kind] ?? 'other', size: info.size }
  }

  async lstat(path, opts, signal) {
    throwIfAborted(signal, 'lstat')
    const target = await this.resolve(path, opts)
    const info = await this.probe(target, 'lstat', signal, false)
    if (info === undefined) return undefined
    const type = info.kind === 'l' ? 'symlink' : TYPES[info.kind] ?? 'other'
    return { version: FsVersion(info.version), type, size: info.size }
  }

  async regularFile(target, verb, signal) {
    throwIfAborted(signal, verb)
    const info = await this.probe(target, verb, signal)
    if (info === undefined) throw new FsError(`cannot ${verb} "${target.displayPath}": not found`, 'FS_NOT_FOUND')
    if (info.kind !== 'f') throw new FsError(`cannot ${verb} "${target.displayPath}": not a regular file`, 'FS_NOT_REGULAR_FILE')
    return info
  }

  /**
   * 从 offset 起读 length 字节；第一次读回来的 stat（大小、版本）一并返回。tooLarge 给出时第一块就按总大小判断，
   * 超了不再往下读。不存在、是目录、不是普通文件由沙箱按错误码报（同 stat）。
   */
  async readWindow(target, offset, length, signal, tooLarge) {
    const chunks = []
    let read = 0
    let stat
    while (read < length) {
      const want = Math.min(READ_CHUNK, length - read)
      const reply = await this.call('read', target, { offset: offset + read, length: want }, 'read', signal)
      stat ??= reply.stat
      if (tooLarge !== undefined && stat !== undefined && stat.size > tooLarge) {
        throw new FsError(`cannot read "${target.displayPath}": ${stat.size} bytes exceeds the ${tooLarge}-byte limit`, 'FS_TOO_LARGE')
      }
      const bytes = Buffer.from(reply.data ?? '', 'base64')
      chunks.push(bytes)
      read += bytes.length
      if (bytes.length < want) break
    }
    return { bytes: Buffer.concat(chunks, read), stat }
  }

  /** 整个文件（不超过 maxBytes）连同读时的 stat：一次来回，不先 stat。 */
  async readWhole(target, signal, maxBytes) {
    throwIfAborted(signal, 'read')
    const { bytes, stat } = await this.readWindow(target, 0, maxBytes + 1, signal, maxBytes)
    if (bytes.length > maxBytes) {
      throw new FsError(`cannot read "${target.displayPath}": content exceeds the ${maxBytes}-byte limit`, 'FS_TOO_LARGE')
    }
    return { bytes, stat }
  }

  async readBytes(target, signal, maxBytes) {
    return (await this.readWhole(target, signal, maxBytes)).bytes
  }

  async readByteRange(target, range, signal) {
    throwIfAborted(signal, 'read')
    if (range.length === 0) {
      await this.regularFile(target, 'read', signal)  // 空区间不读，但文件得在
      return new Uint8Array(0)
    }
    return (await this.readWindow(target, range.offset, range.length, signal)).bytes
  }

  async readText(target, signal) {
    return decodeText(await this.readBytes(target, signal, MAX_TEXT_BYTES), 'read', target.displayPath)
  }

  async streamText(target, signal) {
    const text = await this.readText(target, signal)
    return (async function* () { yield text })()
  }

  async listDir(target, signal) {
    throwIfAborted(signal, 'list')
    const { entries } = await this.call('list', target, {}, 'list', signal)
    const base = String(target.targetKey)
    return (entries ?? []).map(entry => {
      const path = posix.join(base, entry.name)
      return {
        name: entry.name,
        type: TYPES[entry.kind] ?? 'other',
        target: { targetKey: FsTargetKey(path), displayPath: path },
        ...entry.kind === 'f' ? { size: entry.size } : {},
      }
    })
  }

  async put(target, content, signal) {
    const data = Buffer.from(content, 'utf8').toString('base64')
    const { stat } = await this.call('write', target, { data }, 'write', signal)
    return FsVersion(stat?.version ?? `missing:${target.targetKey}`)
  }

  async writeText(target, content, expected, signal) {
    return this.withLock(String(target.targetKey), async () => {
      const existing = await this.probe(target, 'write', signal)
      if (existing !== undefined && existing.kind !== 'f') {
        throw new FsError(`cannot write "${target.displayPath}": not a regular file`, 'FS_NOT_REGULAR_FILE')
      }
      if (expected?.kind === 'replaceIfVersion') {
        if (existing === undefined) throw new FsError(`cannot write "${target.displayPath}": file no longer exists`, 'FS_STALE_VERSION')
        if (existing.version !== expected.version) throw new FsError(`cannot write "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
      } else if (expected?.kind === 'createIfAbsent' && existing !== undefined) {
        throw new FsError(`cannot overwrite existing "${target.displayPath}" without reading it first`, 'FS_NOT_OBSERVED')
      }
      let before = null
      if (existing !== undefined && existing.size < DIFF_BASIS_MAX_BYTES) {
        before = await this.readText(target, signal).then(normalizeLineEndings, () => null)
      }
      throwIfAborted(signal, 'write')
      const version = await this.put(target, content, signal)
      return { operation: existing === undefined ? 'create' : 'update', version, before, after: normalizeLineEndings(content) }
    })
  }

  async editText(target, edit, expected, signal) {
    return this.withLock(String(target.targetKey), async () => {
      // 读一次就够：读回来的 stat 带着版本，不存在、不是普通文件由沙箱报错
      let whole
      try {
        whole = await this.readWhole(target, signal, MAX_TEXT_BYTES)
      } catch (error) {
        if (error instanceof FsError && error.code === 'FS_NOT_FOUND') {
          throw new FsError(`cannot edit "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
        }
        throw error
      }
      if (expected && whole.stat?.version !== expected.version) {
        throw new FsError(`cannot edit "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
      }
      const raw = decodeText(whole.bytes, 'edit', target.displayPath)
      const { before, after, written } = applyLiteralEdit(raw, edit, target.displayPath)
      throwIfAborted(signal, 'edit')
      const version = await this.put(target, written, signal)
      return { version, before, after }
    })
  }
}

export const name = 'knowwork-remote-fs'
