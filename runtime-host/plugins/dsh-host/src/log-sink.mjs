/**
 * 日志输出：官方最小配置为了把 stdout 留给协议，没有挂任何日志输出，插件里的 ctx.logger 调用都被丢弃。
 * 这里用 cordis logger 的 exporter（官方启动代码同样用它收集启动诊断），把 warn 以上的日志按 JSON 行写到 stderr，
 * 由容器日志采集。stdout 只用于就绪行。
 */

import { format } from 'node:util'

export const name = 'knowwork-log-sink'

const LEVEL = { error: 1, warn: 2, info: 3, debug: 4 }

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ level?: string }} [config] */
export function apply(ctx, config) {
  const max = LEVEL[config?.level ?? process.env.KNOWWORK_LOG_LEVEL ?? 'warn'] ?? LEVEL.warn
  ctx.effect(() => ctx.logger.exporter({
    levels: { default: max },
    export: ({ ts, name: logger, type, args }) => {
      process.stderr.write(`${JSON.stringify({ ts, level: type, logger, message: format(...args) })}\n`)
    },
  }), 'knowwork.log-sink')
}
