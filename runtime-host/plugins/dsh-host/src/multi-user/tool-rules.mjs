/**
 * 多用户适配层（四）：工具调用规则。
 *
 * 官方工具有些选项在多用户下暂时不支持（例如定时任务投到「新开的对话」：新对话要由业务服务建、定归属、发令牌），
 * 有些写法容易让模型出错、换一种写法就能整类消掉（例如定时任务的时间自己换算成 UTC）。在官方 `tools/pre-execute`
 * 里按工具名拦住，带一句给模型看的原因让它换个写法再调；官方工具本身不改。
 */

/** 几条规则依次看，第一条给出原因的就拒绝（同一个工具要守几条规则时用）。 */
export function allOf(...rules) {
  return args => rules.reduce((reason, rule) => reason ?? rule(args), undefined)
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {Record<string, (args: any) => string | undefined>} rules 工具名 → 规则；返回原因即拒绝这次调用
 */
export function applyToolRules(ctx, rules) {
  ctx.on('tools/pre-execute', async (exec, next) => {
    const reason = rules[exec.name]?.(exec.arguments ?? {})
    return reason === undefined ? next() : { kind: 'deny', reason }
  })
}
