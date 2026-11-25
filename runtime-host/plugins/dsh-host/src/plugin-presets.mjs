/**
 * 按会话的预置插件装官方 Agent 预设（dsh-agent-preset-registry）。
 *
 * 官方按会话区分能力的机制是预设：一个进程同时跑几种预设，会话建的时候选一种。我们的会话由 http-host 自己建
 * （ctx.agents.create / resume），不经官方会话控制器，所以照它的写法在这里补上「装预设」这一步
 * （dsh-api-session-controller 0.2.2-rc.5 的 composeAgent：meta.agentPreset 加 setup 里 agentPresets.mount）。
 *
 * - 一段会话只能选一个预设，而启用的插件是一个集合：预设按「组合」来，名字是排好序的键的摘要，第一次用到时用官方登记处
 *   公开的 register 登记，同组合的会话共用一份定义；
 * - 没有任何插件的会话用空预设 kw-none：现有挂在全局的插件一样都不挪，行为和没接预设时一样；
 * - 预设由合同决定，宿主不记：每次开、恢复会话都按业务服务带来的清单装。恢复时清单变了就按新的装，历史不受影响；
 * - 子 Agent 不用管：官方 dsh-subagent 自己从父会话接预设；
 * - 官方的 tools.restrict 管不到预设里的工具（会话策略的 deny 对它们无效）：一段会话能不能用某个插件，只由业务服务
 *   放不放进清单决定。
 */

import { createHash } from 'node:crypto'
import { BUILTIN_PLUGINS } from './plugin-catalog.mjs'

export const EMPTY_PRESET = 'kw-none'

/** 这组插件键对应的预设名（键已排序去重） */
export function presetIdOf(keys) {
  if (keys.length === 0) return EMPTY_PRESET
  return `kw-${createHash('sha256').update(keys.join('\n')).digest('hex').slice(0, 16)}`
}

/** 请求里有目录不认识的插件键 */
export class UnknownPlugin extends Error {
  constructor(key) {
    super(`宿主不认识插件 ${key}（业务服务与宿主的版本可能不一致）`)
    this.key = key
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {Record<string, { rows: readonly object[] }>} [extra] 目录之外再认的插件（测试、部署自己加的）
 */
export function createPluginPresets(ctx, extra = {}) {
  const catalog = { ...BUILTIN_PLUGINS, ...extra }
  /** 预设名 → 登记完成的承诺；同组合同时来的请求等同一次登记 */
  const registered = new Map()

  function register(presets, id, keys) {
    const found = registered.get(id)
    if (found !== undefined) return found
    const rows = keys.flatMap(key => catalog[key].rows.map(row => structuredClone(row)))
    const task = presets.register({ id, plugins: rows }).then(() => id)
    registered.set(id, task)
    // 登记失败（插件起不来）不记住：下一次再试，这一次的会话开不了
    task.catch(() => { registered.delete(id) })
    return task
  }

  return {
    /** 宿主的组合里有没有官方预设登记处（没有时带了插件的会话开不了，不带的照旧） */
    get available() { return ctx.get('agentPresets') !== undefined },
    /**
     * 这段会话建 Agent 要带的东西；登记处不在且没有插件时返回空（照旧建）。
     * @param {string[]} keys 排序去重后的插件键
     * @returns {Promise<{ preset?: string, setup?: (agentCtx: object) => Promise<void> }>}
     */
    async compose(keys) {
      const unknown = keys.find(key => catalog[key] === undefined)
      if (unknown !== undefined) throw new UnknownPlugin(unknown)
      const presets = ctx.get('agentPresets')
      if (presets === undefined) {
        if (keys.length > 0) throw new Error('宿主没有挂官方预设登记处，带插件的会话开不了')
        return {}
      }
      // 用 ctx.get 取：http-host 不把登记处声明成必需的依赖（没有它时不带插件的会话照旧能开）
      const preset = await register(presets, presetIdOf(keys), keys)
      return { preset, setup: async (agentCtx) => { await presets.mount(agentCtx, preset) } }
    },
  }
}
