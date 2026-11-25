/**
 * 用户在输入框敲 `/` 的命令（官方 dsh-commands）：按会话列出、按会话执行。服务端宿主经 HTTP（http-host.mjs）、
 * 桌面端的本机宿主经主进程的通道（桌面端的 session-entry）都用这一份，两端只差送达方式。
 *
 * - 命令由插件登记（官方 /goal 等，预设里挂的按会话生效；上传的插件在这个人自己的宿主里登记的也在）；
 * - 执行照官方：直接作用在这段会话的 Agent 上，不变成发给模型的消息；官方把 command/run、command/done 记进会话日志，
 *   业务服务从事件里画出命令气泡，刷新后还在；
 * - 有几个官方命令不放出来，按官方给的稳定编号（definitionId）认，不按名字（名字插件能改）：
 *   - 会改宿主范围设置或把东西送到宿主之外的（权限档、导出会话日志到宿主本机、给官方发反馈），多人共用的服务端宿主上不对；
 *   - /plan（规划模式）：计划做完要用户在审阅卡片上批准才退出，我们的界面还没有这张卡片，进去就出不来（只能 /plan off），
 *     桌面端的本机宿主组合里挂着它，等界面做了再放；
 * - 先不收附件：命令行之外不带图片和文件。
 */

import { HostError } from './intake.mjs'

/** 不放出来的官方命令（definitionId，见官方 @deepseek-ai/dsh-commands/builtin）。 */
const HIDDEN = new Set([
  '@deepseek-ai/dsh-permission-presets',
  '@deepseek-ai/dsh-session-log-export',
  '@deepseek-ai/dsh-command-feedback',
  '@deepseek-ai/dsh-plan-mode',
])
const MAX_LINE = 4000
const MAX_COMMANDS = 100
const RUN_TIMEOUT_MS = 60_000

const visible = (descriptor) => descriptor.definitionId === undefined || !HIDDEN.has(String(descriptor.definitionId))

/** 交给业务服务的样子：名字、说明、输入提示；definitionId 留着，界面认官方命令（例如给 /goal 写中文说明）用 */
function viewOf(descriptor) {
  return {
    name: String(descriptor.name),
    description: String(descriptor.description ?? '').slice(0, 500),
    ...(descriptor.input?.hint ? { hint: String(descriptor.input.hint).slice(0, 200) } : {}),
    ...(descriptor.definitionId === undefined ? {} : { definitionId: String(descriptor.definitionId) }),
  }
}

/**
 * @param {() => any} service 取官方命令服务（ctx.get('commands')）；组合里没挂时返回 undefined
 */
export function createCommands(service) {
  const commands = () => service()

  /** 这段会话的 Agent 能用的命令（含按会话的，官方 list 已按 Agent 合并好） */
  function list(agent) {
    const runtime = commands()
    if (runtime === undefined) return { commands: [] }
    return { commands: runtime.list(agent).filter(visible).slice(0, MAX_COMMANDS).map(viewOf) }
  }

  /** 执行一行命令。不认识的（语法不对、没这个命令、被藏起来的）报 404 command_unknown，不当成提问发给模型 */
  async function run(agent, body) {
    const line = body?.line
    if (typeof line !== 'string' || !line.startsWith('/') || line.length > MAX_LINE) {
      throw new HostError(400, 'invalid_request', `line 必须是 / 开头、不超过 ${MAX_LINE} 个字符的命令行`)
    }
    const runtime = commands()
    const name = /^\/([a-z0-9_-]+)(?:\s|$)/.exec(line)?.[1]
    const found = runtime === undefined || name === undefined ? undefined : runtime.find(agent, name)
    if (found === undefined || !visible(found)) throw new HostError(404, 'command_unknown', `没有 /${name ?? ''} 这个命令`)
    const signal = AbortSignal.timeout(RUN_TIMEOUT_MS)
    const execution = await runtime.execute(agent, line, [], signal)
    if (execution === undefined) throw new HostError(404, 'command_unknown', `没有 /${name} 这个命令`)
    const { result } = execution
    return {
      commandId: String(execution.commandId),
      kind: result.kind === 'error' ? 'error' : 'success',
      ...(result.text === undefined ? {} : { text: String(result.text) }),
    }
  }

  return { list, run }
}
