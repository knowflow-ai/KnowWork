/**
 * @ 了扩展登记的东西（例如数据源），而它在别的回答方式里效果更好时，输入框上方提示可以切过去（不强制，照样能发）。
 * 只提示这个人能选的方式；已经是那种方式、或输入框固定了回答方式（嵌在别的页面里）时不提示。
 */
import type { AnswerMode, AnswerModeOption } from '../../../api/conversations';
import type { Mention, MentionExtension } from '../../../api/mentions';

export interface ModeSuggestion {
  /** 扩展类型名，例如「数据源」 */
  readonly kind: string;
  readonly mode: AnswerModeOption;
}

export function suggestMode(
  mentions: readonly Mention[],
  extensions: readonly MentionExtension[],
  current: AnswerMode,
  modes: readonly AnswerModeOption[],
): ModeSuggestion | null {
  for (const mention of mentions) {
    if (mention.type !== 'extension') continue;
    const extension = extensions.find((e) => e.kind === mention.kind);
    const mode = modes.find((m) => m.id === extension?.answerMode);
    if (extension && mode && mode.id !== current) return { kind: extension.label, mode };
  }
  return null;
}
