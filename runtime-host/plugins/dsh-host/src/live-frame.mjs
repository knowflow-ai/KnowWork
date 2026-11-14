/**
 * 官方逐字帧的整理（纯函数，不依赖 DSH 的包）。两处用同一份：宿主（http-host）把它转给业务服务；在用户设备上执行的对话，
 * 设备那一头拿官方会话接口给的同一种帧、用同一个函数整理后送给业务服务。帧的样子要改只改这里。
 */

/**
 * 官方逐字帧 → 转给业务服务的样子：开始（带轮次、步）、正文片段、思考片段、开始写某个工具调用（只报一次工具名）、结束。
 * 思考片段照官方聊天界面的做法边想边显示：开着思考的模型要先想几秒到几十秒才写正文，不转的话这段时间页面上一个字都没有。
 * 工具参数的片段不转：模型把整个文件写进 write 的参数时要好十几秒，报一次工具名，页面就能写「正在写文件」，
 * 参数本身不转（量大，半截的参数也没法给人看）。
 * @param {{ type: string, attemptId?: string, turn?: number, step?: number,
 *   chunk?: { type: string, text?: string, index?: number, name?: string } }} frame
 * @param {Map<string, Set<number>>} announced
 */
export function liveFrame(frame, announced) {
  if (frame.type === 'start') return { type: 'start', attemptId: frame.attemptId, turn: frame.turn, step: frame.step }
  if (frame.type === 'end') {
    announced.delete(frame.attemptId)
    return { type: 'end', attemptId: frame.attemptId }
  }
  const chunk = frame.type === 'chunk' ? frame.chunk : undefined
  if (chunk?.type === 'text-delta' && typeof chunk.text === 'string') {
    return { type: 'text', attemptId: frame.attemptId, text: chunk.text }
  }
  if (chunk?.type === 'reasoning-delta' && typeof chunk.text === 'string' && chunk.text) {
    return { type: 'reasoning', attemptId: frame.attemptId, text: chunk.text }
  }
  if (chunk?.type === 'tool-call-delta' && typeof chunk.name === 'string' && chunk.name) {
    const seen = announced.get(frame.attemptId) ?? new Set()
    if (seen.has(chunk.index)) return undefined
    announced.set(frame.attemptId, seen.add(chunk.index))
    return { type: 'tool', attemptId: frame.attemptId, name: chunk.name }
  }
  return undefined
}
