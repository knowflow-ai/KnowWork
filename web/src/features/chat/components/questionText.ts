/** 提问卡片的显示文字：推荐标记与回答摘要。 */
import type { QuestionAnswerView } from '../events/types';

// 官方约定推荐选项的标签末尾加「(Recommended)」；中文模型可能写成「（推荐）」
const RECOMMENDED = /\s*[(（](recommended|推荐)[)）]\s*$/i;

/** 选项标签去掉推荐标记后显示；提交时仍用原标签。 */
export function displayLabel(label: string): { readonly text: string; readonly recommended: boolean } {
  const match = RECOMMENDED.exec(label);
  return match
    ? { text: label.slice(0, match.index) || label, recommended: true }
    : { text: label, recommended: false };
}

/** 一题的回答写成一句话：选项（去掉推荐标记）与文字用顿号连起来。 */
export function answerText(answer?: QuestionAnswerView): string {
  if (!answer) return '';
  const parts = [...answer.selected.map((label) => displayLabel(label).text), answer.custom ?? ''].filter(
    Boolean,
  );
  return parts.length > 0 ? parts.join('、') : '（跳过）';
}
