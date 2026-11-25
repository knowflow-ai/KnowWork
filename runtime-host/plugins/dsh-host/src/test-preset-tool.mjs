/** 只在测试时用：一个最小的工具插件，当预设里的条目，看它是不是只在带了它的会话里可见（tests/plugin-presets.test.mjs）。 */
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'knowwork-test-preset-tool'
export const inject = ['tools']

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'kw_preset_probe',
    description: '原样返回输入的文字（预设测试用）。',
    parameters: { text: { type: 'string', required: true, description: '要返回的文字' } },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: String(value) }] },
    async execute(args) { return `preset: ${args.text}` },
  }))
}
