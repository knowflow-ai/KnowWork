/**
 * 定时任务的多用户适配（官方 @deepseek-ai/dsh-schedule 0.2.2-rc.1）。用的是本目录的通用适配层，这里只有规则：
 * - 官方 ScheduleService 原样，继承它补归属：建任务记属主（owned-service 的 create），列表只给自己的（list，照官方
 *   resolveListPage 的校验与分页），查看、编辑不是自己的照官方返回「找不到」（guard）；
 * - 到点时官方经 `schedule/resolve-agent` 要活会话：改道给业务服务激活（activation），官方再照常投递；
 * - 投递目标第一版只支持「这个对话」（this_session）：新开对话（new_once、new）要业务服务建对话、定归属，先用工具
 *   调用规则拦住，让模型改用 this_session；
 * - 时间只收按时区写的本地时间（DTSTART;TZID=…）：官方两种写法都收，但写成 UTC（以 Z 结尾）要模型自己换算时区，
 *   实测把洛杉矶时间换成 UTC 时多加了一小时。按时区写本地时间就不用换算，任务结果里的 scheduledAtLocal 也是用户的
 *   本地时间（官方按 rrule 的时区算）。UNTIL 官方规定写 UTC，不管它。
 * 官方的定时任务工具（@deepseek-ai/dsh-tool-schedule）原样挂，不改；配置里停用官方 schedule 那一行，换成这里。
 * 配置照官方 ScheduleService.Config（类继承下来）；属主账本在属主服务 knowworkOwners 里，配置里放在这一行前面。
 */

import ScheduleService, { DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT, ScheduleInputError } from '@deepseek-ai/dsh-schedule'
import { activateThroughBusiness, createActivationClient } from './activation.mjs'
import { ownedService } from './owned-service.mjs'
import { allOf, applyToolRules } from './tool-rules.mjs'

const KIND = 'schedule'
const THIS_SESSION_ONLY = 'KnowWork 里定时任务的结果投到发起它的这个对话：target 请改用 this_session 再调一次。'
const LOCAL_TIME_ONLY = 'DTSTART 请按用户的时区写本地时间，不要自己换算成 UTC：写成 DTSTART;TZID=<时区>:YYYYMMDDTHHMMSS，'
  + '时区用时间读数里写的用户时区；用户明确要 UTC 时写 TZID=Etc/UTC。改好再调一次。'

const notFound = request => ({ id: request.id, code: 'schedule_not_found' })

/** 照官方 list：先按启用状态筛，再分页；limit、offset 的校验与报错同官方 resolveListPage。 */
function page([request = {}], items) {
  const limit = request.limit ?? DEFAULT_LIST_LIMIT
  const offset = request.offset ?? 0
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_LIST_LIMIT || !Number.isSafeInteger(offset) || offset < 0) {
    throw new ScheduleInputError('invalid_rule', `limit must be 1-${MAX_LIST_LIMIT} and offset must be a non-negative integer.`)
  }
  const matched = items.filter(entry => request.active === undefined || (entry.status === 'active') === request.active)
  const shown = matched.slice(offset, offset + limit)
  return { items: shown, shown: shown.length, total: matched.length, limit, offset }
}

const OwnedSchedule = ownedService(ScheduleService, {
  kind: KIND,
  create: { create: { id: result => result?.id } },
  list: { list: { all() { return this.catalog() }, id: entry => entry.id, page } },
  guard: {
    view: { id: request => request.id, notFound },
    edit: { id: request => request.id, notFound },
  },
})

const targetRule = args => args.target === undefined || args.target === 'this_session' ? undefined : THIS_SESSION_ONLY
/** DTSTART 那一行写成 UTC（DTSTART:…Z）就拦下；没给 rrule（编辑时只改别的）不管 */
const localTimeRule = args => typeof args.rrule === 'string' && /^\s*DTSTART:\d{8}T\d{6}Z/im.test(args.rrule)
  ? LOCAL_TIME_ONLY
  : undefined
const rules = allOf(targetRule, localTimeRule)

export default class KnowWorkSchedule extends OwnedSchedule {
  constructor(ctx, config) {
    super(ctx, config)
    applyToolRules(ctx, { schedule_create: rules, schedule_edit: rules })
    activateThroughBusiness(ctx, { event: 'schedule/resolve-agent', kind: KIND, client: createActivationClient() })
  }
}

export const name = 'knowwork-schedule'
