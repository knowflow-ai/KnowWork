/**
 * 预置插件目录：插件键 → 装进预设的官方插件条目（同 cordis 配置里的一行：name、可选的 id 与 config）。
 * 键与业务服务那份清单（server 的 plugins/catalog.py）是同一批；业务服务决定这段对话带哪几个键，这里只管怎么装。
 * 只放我们审过、锁了版本的官方插件：预设里的代码在共享宿主进程里跑，不是安全边界。
 * 条目要一起用的全局服务（例如目标的 dsh-goal）写在环境层的配置里（host-bundle/cordis.patch.yml），不在这里。
 */

/** @type {Readonly<Record<string, { rows: readonly object[] }>>} */
export const BUILTIN_PLUGINS = Object.freeze({
  // 长任务目标：官方的目标工具（get_goal、create_goal、update_goal）。照官方 standard 预设放在预设里；目标的服务
  // dsh-goal 和自动续跑 dsh-goal-round-driver 挂在环境层的全局（只有开了这个插件的会话建得出目标，续跑只管有目标的会话），
  // 每一轮开始前经 goal-gate.mjs 请业务服务核对。目标按会话存在会话日志里，不存宿主本机
  // 官方的 /goal 命令（dsh-command-goal）也放在预设里：开了这个插件的会话里用户才能敲 /goal 看、改、暂停、清除目标
  goal: {
    rows: [
      { id: 'tool-goal', name: '@deepseek-ai/dsh-tool-goal' },
      { id: 'command-goal', name: '@deepseek-ai/dsh-command-goal' },
    ],
  },
})

const KEY = /^[a-z][a-z0-9-]{0,47}$/
export const MAX_PLUGINS = 32

/** 合同里的 plugins：去重、排序后的键；格式不对抛错（message 给业务服务看） */
export function pluginKeysOf(raw) {
  if (raw === undefined) return []
  if (!Array.isArray(raw) || raw.length > MAX_PLUGINS || !raw.every(key => typeof key === 'string' && KEY.test(key))) {
    throw new Error(`plugins 必须是不超过 ${MAX_PLUGINS} 个插件键的数组`)
  }
  return [...new Set(raw)].sort()
}
