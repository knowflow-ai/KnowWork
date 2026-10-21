/**
 * 文本文件的解码、行尾与字面编辑：逐条照官方 dsh-fs-local 的行为（那几个函数没有导出，只能照抄语义）。
 * - 读：前 8KB 有 NUL 当二进制，UTF-8 严格解码，原样返回不做行尾规整；
 * - 编辑：先把内容与 old/new 都规整成 LF 再匹配，写回时按读到的行尾风格还原；
 *   没找到报 FS_EDIT_NOT_FOUND，不是 replaceAll 却出现多处报 FS_AMBIGUOUS_EDIT。
 */

import { FsError } from '@deepseek-ai/dsh-fs'

const BINARY_SAMPLE_BYTES = 8192

export function decodeText(bytes, verb, displayPath) {
  if (bytes.subarray(0, BINARY_SAMPLE_BYTES).includes(0)) {
    throw new FsError(`cannot ${verb} "${displayPath}": binary file`, 'FS_NOT_TEXT')
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new FsError(`cannot ${verb} "${displayPath}": invalid UTF-8 text`, 'FS_NOT_TEXT')
  }
}

export function normalizeLineEndings(content) {
  return content.replaceAll('\r\n', '\n')
}

function detectLineEndings(raw) {
  const sample = raw.slice(0, 4096)
  const crlf = sample.split('\r\n').length - 1
  return crlf > sample.split('\n').length - 1 - crlf ? 'CRLF' : 'LF'
}

function restoreLineEndings(content, lineEndings) {
  return lineEndings === 'LF' ? content : normalizeLineEndings(content).split('\n').join('\r\n')
}

function countOccurrences(content, needle) {
  let count = 0
  for (let index = content.indexOf(needle); index !== -1; index = content.indexOf(needle, index + needle.length)) {
    count += 1
  }
  return count
}

/**
 * @param {string} raw 文件当前内容（原样）
 * @param {{ oldString: string, newString: string, replaceAll: boolean }} edit
 * @returns {{ before: string, after: string, written: string }} before/after 为 LF 规整后的内容，written 是要写回的原风格文本
 */
export function applyLiteralEdit(raw, edit, displayPath) {
  if (raw.includes('\0')) throw new FsError(`cannot edit "${displayPath}": binary file`, 'FS_NOT_TEXT')
  const before = normalizeLineEndings(raw)
  const oldNorm = normalizeLineEndings(edit.oldString)
  if (oldNorm.length === 0) throw new FsError('old_string must be a non-empty string', 'FS_EDIT_NOT_FOUND')
  const replacements = countOccurrences(before, oldNorm)
  if (replacements === 0) throw new FsError(`old_string was not found in "${displayPath}"`, 'FS_EDIT_NOT_FOUND')
  if (!edit.replaceAll && replacements > 1) {
    throw new FsError(`old_string matched ${replacements} times in "${displayPath}"; provide a more specific old_string or set replace_all to true`, 'FS_AMBIGUOUS_EDIT')
  }
  const after = before.split(oldNorm).join(normalizeLineEndings(edit.newString))
  return { before, after, written: restoreLineEndings(after, detectLineEndings(raw)) }
}
