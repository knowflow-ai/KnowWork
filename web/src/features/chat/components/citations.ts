export const CITE_PREFIX = '#kw-cite-';

// 后端要求模型写 [n]；兼容 [ID:n]、[^n]。后面紧跟「(」是 Markdown 链接、紧跟「:」是脚注定义，都不算引用。
const MARK = /\[(?:ID:|\^)?(\d{1,4})\](?![(:])/g;

/** 把引用标记改写成内部链接，再由 a 组件渲染成上标。 */
export function markCitations(text: string): string {
  return text.replace(MARK, (_, n: string) => `[${n}](${CITE_PREFIX}${n})`);
}

/** 回答里引用到的编号，去重并保持首次出现的顺序。 */
export function citedNumbers(text: string): readonly number[] {
  return [...new Set([...text.matchAll(MARK)].map((match) => Number(match[1])))];
}
