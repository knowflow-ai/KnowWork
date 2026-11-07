/**
 * 多用户适配层（三）：属主级资源服务。
 *
 * 官方有一类插件把属主级的东西放在一个服务里，工具只调这个服务（定时任务：ScheduleService 存全部任务，schedule_*
 * 工具调 create / list / view / edit）。官方眼里全宿主只有一个属主，列出来的是「全部」。适配办法是继承官方服务类，
 * 按声明的规则包几个方法，官方逻辑一行不改：
 * - create：官方建好后在属主账本里记一笔（调用者的空间与人，连同发起它的对话）；
 * - list：取全部（all），只留调用者的，再交给 page 分页——先过滤再分页，总数和翻页才对；
 * - guard：按 id 读写单个资源前核对归属，不是调用者的照官方「找不到」的样子返回（notFound）；
 * - prepare：调官方之前改写参数（可选）。
 * 没有调用者的调用（宿主内部：定时器到点、启动时读全部）原样放行；属主不明的会话不会有调用者（caller.mjs）。
 * 账本在属主服务 `knowworkOwners`（owners.mjs）里，适配出来的类自动依赖它。
 *
 * 官方方法改名或删掉时，类一定义就报错，升级 DSH 时随测试暴露，不等用到才发现。
 */

import { currentCaller, sameOwner } from './caller.mjs'

const OWNERS = 'knowworkOwners'

/** 官方的依赖声明加上属主服务（数组和对象两种写法都照官方原样保留）。 */
function withOwners(inject) {
  if (inject === undefined) return [OWNERS]
  if (Array.isArray(inject)) return inject.includes(OWNERS) ? inject : [...inject, OWNERS]
  return { ...inject, [OWNERS]: { required: true } }
}

/**
 * @template T
 * @param {T} Base 官方服务类
 * @param {{
 *   kind: string,
 *   create?: Record<string, { id: (result: any) => string | undefined }>,
 *   list?: Record<string, { all: (this: any, args: any[]) => Promise<any[]>, id: (item: any) => string, page: (args: any[], items: any[]) => any }>,
 *   guard?: Record<string, { id: (...args: any[]) => string, notFound: (...args: any[]) => any }>,
 *   prepare?: Record<string, (args: any[], caller: import('./caller.mjs').Caller) => any[]>,
 * }} spec
 * @returns {T}
 */
export function ownedService(Base, spec) {
  const { kind } = spec
  class Owned extends Base {
    static inject = withOwners(Base.inject)
  }
  const owners = service => service.ctx.get(OWNERS)
  const owns = (service, caller, id) => id !== undefined && sameOwner(owners(service).ownerOf(kind, id), caller.owner)
  const prepared = (method, args, caller) => spec.prepare?.[method]?.(args, caller) ?? args

  const define = (method, around) => {
    const official = Base.prototype[method]
    if (typeof official !== 'function') {
      throw new Error(`${Base.name}.${method} 不存在：官方接口变了，多用户适配规则要跟着改`)
    }
    Object.defineProperty(Owned.prototype, method, {
      configurable: true,
      writable: true,
      value(...args) {
        const caller = currentCaller()
        return caller === undefined ? official.apply(this, args) : around.call(this, official, args, caller)
      },
    })
  }

  for (const [method, rule] of Object.entries(spec.create ?? {})) {
    define(method, async function (official, args, caller) {
      const result = await official.apply(this, prepared(method, args, caller))
      const id = rule.id(result)
      if (id !== undefined) owners(this).claim(kind, id, caller)
      return result
    })
  }
  for (const [method, rule] of Object.entries(spec.list ?? {})) {
    define(method, async function (_official, args, caller) {
      const all = await rule.all.call(this, args)
      return rule.page(args, all.filter(item => owns(this, caller, rule.id(item))))
    })
  }
  for (const [method, rule] of Object.entries(spec.guard ?? {})) {
    define(method, async function (official, args, caller) {
      return owns(this, caller, rule.id(...args))
        ? official.apply(this, prepared(method, args, caller))
        : rule.notFound(...args)
    })
  }
  return Owned
}
