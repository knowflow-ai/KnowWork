/**
 * 包住一次模型调用的输出流，边放边查（内容检查插件 content-guard.mjs 用）。
 *
 * - 正文、推理按块各自累积，攒够一段（或隔了一会儿）把新增的部分接上前面留着的尾巴送去查；每块留「最长词长 - 1」个字
 *   暂不放出，跨两段切开的词接上尾巴就能整个查到，命中前客户端看不到完整的词；块结束时把剩下的查完、放完；
 * - 工具调用的增量整块扣住，块结束时参数完整（官方组装好的原始 JSON），解开以后键和值都查，通过才连同开头、增量一起放出；
 *   没通过就不放，官方组装不出这个调用，工具不会执行；
 * - 命中（或检查接口不通）以后不再放任何内容，继续读上游直到 usage（照常转出，用量不漏记），再以 error 结束：
 *   失败码 CONTENT_BLOCKED（或 CHECK_UNAVAILABLE），话术是管理员写的阻断提示。官方重试不认这两个码，不会重来。
 */

/** 新增多少个字就送去查一次；不到这么多、但离上次查过了这么久也查，免得模型慢的时候字一直憋着 */
const CHECK_EVERY_CHARS = 64
const CHECK_EVERY_MS = 200
const MAX_JSON_DEPTH = 32

/** @typedef {{ code: 'CONTENT_BLOCKED' | 'CHECK_UNAVAILABLE', message: string }} Ending */

export function finishWith(ending) {
  return { type: 'finish', reason: { kind: 'error', failure: { code: ending.code, message: ending.message } } }
}

/**
 * 模型网关替个人专属宿主查到的（业务服务 gateway/content_guard.py，那边的 BLOCKED、UNAVAILABLE 两个开头，改了两边一起改）：
 * 官方适配器把它当成这次调用失败，失败文字是「开头: 阻断提示」，HTTP 错误时被包成「状态: {"message": ...}」。
 * 认出来就换成和这里拦下时一样的结束，员工看到的是同一句话。
 */
const GATEWAY_ENDINGS = [['knowwork-content-blocked', 'CONTENT_BLOCKED'], ['knowwork-check-unavailable', 'CHECK_UNAVAILABLE']]

/** @returns {Ending | undefined} */
export function gatewayEnding(text) {
  if (typeof text !== 'string') return undefined
  let found = text
  const brace = text.indexOf('{')
  if (brace >= 0) {
    try {
      const body = JSON.parse(text.slice(brace))
      if (typeof body?.message === 'string') found = body.message
    } catch {
      // 不是包起来的 JSON，就按原文认
    }
  }
  for (const [marker, code] of GATEWAY_ENDINGS) {
    const at = found.indexOf(`${marker}:`)
    if (at >= 0) return { code, message: found.slice(at + marker.length + 1).trim() }
  }
  return undefined
}

/** 包住官方的输出流：网关拦下的那种失败换成我们的结束原因，别的原样放 */
export async function* gatewayEndings(source) {
  for await (const chunk of source) {
    const failure = chunk?.type === 'finish' && chunk.reason?.kind === 'error' ? chunk.reason.failure : undefined
    const ending = gatewayEnding(failure?.message)
    yield ending === undefined ? chunk : finishWith(ending)
  }
}

/** 工具参数里要查的文字：解开 JSON，取全部的键和字符串值（转义过的中文解开后才查得到）；解不开就查原文 */
export function argumentTexts(raw) {
  if (typeof raw !== 'string' || raw.length === 0) return []
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return [raw]
  }
  const found = []
  const walk = (value, depth) => {
    if (depth > MAX_JSON_DEPTH) return
    if (typeof value === 'string') found.push(value)
    else if (Array.isArray(value)) for (const item of value) walk(item, depth + 1)
    else if (typeof value === 'object' && value !== null) {
      for (const [key, item] of Object.entries(value)) {
        found.push(key)
        walk(item, depth + 1)
      }
    }
  }
  walk(parsed, 0)
  return found.length === 0 ? [] : [found.join('\n')]
}

function newBlock(start, now) {
  return { start, started: false, points: [], checked: 0, released: 0, checkedAt: now, held: [] }
}

function* release(block, upTo) {
  if (!block.started) {
    yield block.start
    block.started = true
  }
  if (upTo > block.released) {
    const type = block.start.blockType === 'reasoning' ? 'reasoning-delta' : 'text-delta'
    yield { type, index: block.start.index, text: block.points.slice(block.released, upTo).join('') }
    block.released = upTo
  }
}

/**
 * @param {AsyncIterable<any>} source 官方的输出流
 * @param {{ holdback: number, verify: (direction: string, texts: string[], tool?: string) => Promise<Ending | undefined>,
 *   now?: () => number }} options verify 查一段文字，干净返回 undefined
 */
export async function* guardOutput(source, { holdback, verify, now = Date.now }) {
  const blocks = new Map()
  /** @type {Ending | undefined} */
  let ending

  async function scan(block) {
    const total = block.points.length
    if (total <= block.checked) return undefined
    // 从上次查到的位置往回退 holdback 个字：结束在新增部分里的词，开头最早就在这里
    const from = Math.max(0, block.checked - holdback)
    const found = await verify('output', [block.points.slice(from, total).join('')])
    block.checked = total
    block.checkedAt = now()
    return found
  }

  for await (const chunk of source) {
    if (ending !== undefined) {
      // 命中之后：内容一律不放，只把 usage 转出去，等上游的 finish 再换成我们的结束原因
      if (chunk.type === 'usage') yield chunk
      if (chunk.type === 'finish') {
        yield finishWith(ending)
        return
      }
      continue
    }
    if (chunk.type === 'block-start') {
      blocks.set(chunk.index, newBlock(chunk, now()))
      continue
    }
    const block = blocks.get(chunk.index)
    if (chunk.type === 'text-delta' || chunk.type === 'reasoning-delta') {
      if (block === undefined) {
        yield chunk
        continue
      }
      for (const point of chunk.text) block.points.push(point)
      const fresh = block.points.length - block.checked
      if (fresh < CHECK_EVERY_CHARS && now() - block.checkedAt < CHECK_EVERY_MS) continue
      ending = await scan(block)
      if (ending === undefined) yield* release(block, Math.max(block.released, block.checked - holdback))
      continue
    }
    if (chunk.type === 'tool-call-delta') {
      if (block === undefined) yield chunk
      else block.held.push(chunk)
      continue
    }
    if (chunk.type === 'block-end') {
      if (chunk.block?.type === 'tool-call') {
        ending = await verify('tool_args', argumentTexts(chunk.block.arguments), chunk.block.name)
        if (ending !== undefined) continue
        if (block !== undefined) {
          yield block.start
          yield* block.held
        }
        yield chunk
        continue
      }
      if (block !== undefined) {
        ending = await scan(block)
        if (ending !== undefined) continue
        yield* release(block, block.points.length)
      }
      yield chunk
      continue
    }
    yield chunk
  }
  // 上游没有 finish 就断了（官方协议不会这样）：命中过的照样给结束原因
  if (ending !== undefined) yield finishWith(ending)
}
