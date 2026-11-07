/**
 * 用户消息的来源：照官方会话控制器收到远程提问时的写法（user-rpc 来源：rpcId 是这次请求的编号，
 * clientTimeZone 是提问人的时区）。
 *
 * 时区给官方时钟 dsh-time-context 用：它按这个时区写当前时间；消息上没有时区时，读数里会让模型去问用户
 * 「没写时区的日期时间按哪个时区算」。业务服务带来的是用户浏览器的时区；IM、后台会话、工作流这些没有浏览器，
 * 用部署时区（Node 按容器的 TZ 算，部署编排默认 Asia/Shanghai；没设 TZ 时是 UTC）。
 *
 * 时区只是提示，写错了不该挡住提问：不合规的换成部署时区。校验用官方的 canonicalizeTimeZone（官方会话控制器与
 * 时钟用的同一个），别名换成规范名再写进去——官方时钟见到不规范的名字会直接报错，那一步就失败了。
 */

import { randomUUID } from 'node:crypto'
import { canonicalizeTimeZone } from '@deepseek-ai/dsh-util-time'

/** 规范的时区名；不是合规的 IANA 名字（或 UTC）时返回 undefined。 */
export function canonicalTimeZone(value) {
  if (typeof value !== 'string') return undefined
  const resolved = canonicalizeTimeZone(value)
  return resolved.kind === 'accepted' ? resolved.timeZone : undefined
}

/** 部署时区：本进程的时区。 */
export function deploymentTimeZone() {
  return canonicalTimeZone(new Intl.DateTimeFormat().resolvedOptions().timeZone) ?? 'UTC'
}

/** 一条用户消息的来源，带上提问人的时区；没带或不合规时用 fallback。 */
export function userSource(timeZone, fallback) {
  return { kind: 'user', rpcId: randomUUID(), clientTimeZone: canonicalTimeZone(timeZone) ?? fallback }
}

/**
 * 给子 Agent 的任务消息补上时区。官方委派时任务消息的来源只写 `{ kind: 'user' }`，不带时区，官方时钟就在子 Agent
 * 的读数里写「时区不可用，请向用户确认」；这里换成同样写法的 user-rpc 来源。已经带了编号的（有人直接发给子 Agent 的）
 * 和别的来源（父 Agent 后来发的消息、系统补的上下文）不动。
 */
export function withTimeZone(message, timeZone) {
  const source = message.source
  if (source?.kind !== 'user' || 'rpcId' in source) return message
  return { ...message, source: { ...source, rpcId: randomUUID(), clientTimeZone: timeZone } }
}
