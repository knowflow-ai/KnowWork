/**
 * 工作区路径（宿主这边）：对话里的文件只有一个身份——相对这段对话工作区的路径（服务端同一套规矩
 * 在 server/src/knowwork_server/workpath.py）。
 *
 * - 跨进程传的是相对写法（`output/报告.docx`，根是空串）：宿主发给业务服务的沙箱请求、业务服务发来的附件都是它；
 * - 同一个工作区挂在哪是挂载点：宿主看到的是 `/sessions/<会话>`（hostMount，DSH 的文件与执行服务只认路径，路径里带会话
 *   才分得清是哪个容器），模型在会话里看到、命令里写的是沙箱的 `/workspace`（SANDBOX，容器里 /sessions/<会话> 是指向它的
 *   链接）；换算只经这里，插件里不拼路径。
 * 只做字面判定，不是安全边界（同服务端的说明）。
 */

import { posix } from 'node:path'

export const SESSIONS_ROOT = '/sessions'
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const MAX_PATH = 4096

/** 附件目录：系统放入，模型只读 */
export const INPUT = 'input'

/** 规范化一个绝对路径（去掉多余的斜杠、. 与 ..）；不是绝对路径或不合法返回 undefined */
function normalAbsolute(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.includes('\0') || path.length > MAX_PATH) return undefined
  return posix.normalize(`/${path.replace(/^\/+/, '')}`).replace(/\/+$/, '') || '/'
}

/** 跨进程传来的工作区路径（相对写法，根是空串）：规范化后不越出工作区才算数，否则 undefined */
export function decode(relative) {
  if (relative === '') return ''
  if (typeof relative !== 'string' || relative.startsWith('/') || relative.includes('\0') || relative.length > MAX_PATH) return undefined
  const normal = posix.normalize(relative).replace(/\/+$/, '')
  if (normal === '.') return ''
  if (normal === '..' || normal.startsWith('../')) return undefined
  return normal
}

/** 工作区里的路径是不是在某个文件夹里（或就是它） */
export function within(relative, folder) {
  return folder === '' || relative === folder || relative.startsWith(`${folder}/`)
}

/** 一个挂载点：工作区挂在 root 这个真实位置 */
export function mount(root) {
  return {
    root,
    /** 工作区路径 → 这里的真实路径 */
    at: (relative) => (relative === '' ? root : `${root}/${relative}`),
    /** 这里的真实路径 → 工作区路径；不在这个挂载点下返回 undefined */
    of(path) {
      const normal = normalAbsolute(path)
      if (normal === undefined) return undefined
      if (normal === root) return ''
      return normal.startsWith(`${root}/`) ? normal.slice(root.length + 1) : undefined
    },
  }
}

/** 会话沙箱里的工作区 */
export const SANDBOX = mount('/workspace')

/** 宿主看到的会话工作目录 */
export function hostMount(sessionId) {
  if (!ID_PATTERN.test(sessionId ?? '')) throw new Error(`会话标识不合法：${sessionId}`)
  return mount(`${SESSIONS_ROOT}/${sessionId}`)
}

/**
 * 宿主路径（/sessions/<会话>/...）→ 哪个会话、工作区路径；不是会话目录下的返回 undefined。
 * 文件与执行服务只给路径，会话从路径里认：子 Agent 继承父会话的工作目录，落到同一个容器。
 */
export function locateHostPath(path) {
  const normal = normalAbsolute(path)
  if (normal === undefined) return undefined
  const parts = normal.split('/')
  if (parts[1] !== SESSIONS_ROOT.slice(1) || parts[2] === undefined || !ID_PATTERN.test(parts[2])) return undefined
  const relative = hostMount(parts[2]).of(normal)
  return relative === undefined ? undefined : { sessionId: parts[2], relative, path: normal }
}
