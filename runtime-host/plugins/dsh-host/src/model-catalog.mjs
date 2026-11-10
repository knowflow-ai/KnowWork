/**
 * 可选的对话模型：业务服务把模型目录里启用的对话模型推过来（PUT /v1/models），宿主改官方 dsh-llm-pi-ai 那条路由的
 * models 清单。官方适配器只认配置里声明过的模型（没声明的以 UNKNOWN_MODEL 失败），而模型是管理员在业务服务里随时增删的，
 * 所以运行时经 cordis 的 fiber.update(config, noSave) 换掉内存里的配置：适配器按快照换路由集合，进行中的请求不受影响；
 * noSave 不写回配置文件（容器里配置只读，配置档本身仍只声明「工作空间默认模型」那一条）。
 *
 * 每段对话用哪个模型由官方的会话级选择定（agent.model.select，存在会话里、从下一步生效），见 http-host.mjs 的 sendMessage。
 * 宿主只记得最近一次推来的清单：没推过（例如刚重启）的模型直接拒，业务服务据此重推一次再发。
 */

export const MODEL_ID = /^[A-Za-z0-9_.:-]{1,64}$/
const MAX_MODELS = 200
const MAX_NAME = 200

/**
 * 校验推来的清单：[{ id, name, contextWindow?, vision }]。
 * @param {unknown} value
 * @returns {{ id: string, name: string, contextWindow?: number, vision: boolean }[]}
 */
export function modelListOf(value) {
  if (!Array.isArray(value) || value.length > MAX_MODELS) throw new Error(`models 必须是数组，最多 ${MAX_MODELS} 个`)
  const seen = new Set()
  return value.map((item) => {
    if (typeof item !== 'object' || item === null) throw new Error('models 里每一项都必须是对象')
    const { id, name, contextWindow, vision } = item
    if (typeof id !== 'string' || !MODEL_ID.test(id)) throw new Error(`模型 id 格式不对：${String(id)}`)
    if (seen.has(id)) throw new Error(`模型 id 重复：${id}`)
    seen.add(id)
    if (typeof name !== 'string' || !name || name.length > MAX_NAME) throw new Error(`模型 ${id} 的名称不对`)
    if (contextWindow !== undefined && (!Number.isInteger(contextWindow) || contextWindow < 1)) {
      throw new Error(`模型 ${id} 的上下文长度不对`)
    }
    return { id, name, ...(contextWindow === undefined ? {} : { contextWindow }), vision: vision === true }
  })
}

/**
 * 在适配器配置上换掉这条路由的模型清单：保留配置档里的默认模型那一条（没选模型的会话还用它），其余换成推来的。
 * id 不是字符串的（没求值的表达式）也当默认那条原样保留。不改原对象。
 * @param {{ providers: Record<string, { models?: any[] }> }} current
 * @param {string} provider
 * @param {string} defaultModel
 * @param {ReturnType<typeof modelListOf>} models
 * @param {number} fallbackWindow 推来的模型没写上下文长度时用（同配置档里默认模型的写法）
 */
export function withModels(current, provider, defaultModel, models, fallbackWindow) {
  const route = current?.providers?.[provider]
  if (route === undefined) throw new Error(`适配器配置里没有路由 ${provider}`)
  const kept = (route.models ?? []).filter((m) => typeof m?.id !== 'string' || m.id === defaultModel)
  const synced = models.filter((m) => m.id !== defaultModel).map((m) => ({
    id: m.id,
    name: m.name,
    contextWindow: m.contextWindow ?? fallbackWindow,
    // 声明能收图片的才发图；不能看图的模型在业务服务那边也会收掉 read_image
    input: m.vision ? ['text', 'image'] : ['text'],
  }))
  return { ...current, providers: { ...current.providers, [provider]: { ...route, models: [...kept, ...synced] } } }
}

/**
 * 按配置里的 id 找模型适配器的实例。配置档按包（bundle）组装，条目嵌在包的分组里，loader.resolve 只认带分组路径的 id，
 * 所以把整棵树（含嵌套的分组）走一遍按 id 找。
 */
function adapterFiber(loader, entryId) {
  if (loader === undefined) return undefined
  for (const entry of loader.entries()) {
    if (entry.options?.id === entryId) return entry.fiber
  }
  return undefined
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ entryId: string, provider: string, defaultModel: string }} options
 */
export function createModelCatalog(ctx, { entryId, provider, defaultModel }) {
  let known = new Set([defaultModel])

  return {
    /** 宿主现在能不能用这个模型发请求 */
    knows: (id) => known.has(id),

    /** @param {unknown} value 推来的清单 */
    async sync(value) {
      const models = modelListOf(value)
      const fiber = adapterFiber(ctx.get('loader'), entryId)
      if (fiber === undefined) throw new Error(`没有找到模型适配器 ${entryId}`)
      // 生效配置里 providers 是可在运行时换值的引用（Volatile，.get() 取当前值）：只换它，cordis 把新值提交进运行中的引用，
      // 不重启适配器。用求过值的当前配置拼新配置——原始配置里配置档写的 !!js 还是表达式，fiber.update 不求值
      const live = fiber.effectiveConfig?.providers
      const providers = typeof live?.get === 'function' ? live.get() : live
      const fallbackWindow = Number(process.env.KNOWWORK_MODEL_CONTEXT_WINDOW ?? 131072)
      await fiber.update(withModels({ providers }, provider, defaultModel, models, fallbackWindow), true)
      known = new Set([defaultModel, ...models.map((m) => m.id)])
      return { models: known.size }
    },
  }
}
