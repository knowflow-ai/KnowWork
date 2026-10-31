/**
 * 把回答里的 mermaid 原文画成 SVG。
 * - 库随前端打包、按需加载（几 MB，回答里真有图才拉）；不走 CDN，私有化部署可能连不了外网。
 * - 严格安全模式：图里的文字按纯文本处理，不能执行脚本、不能带点击跳转；单色主题，跟界面一致。
 * - 先检查语法再画：写错了直接 render 会往页面里留一张报错图。写错了先补引号再试一次（模型常见的错：节点文字、
 *   子图标题里有括号、中文标点、空格却没加引号），还不行就交给调用方显示原文。
 */
import type { Mermaid } from 'mermaid';

let loading: Promise<Mermaid> | undefined;
let counter = 0;

function load(): Promise<Mermaid> {
  loading ??= import('mermaid').then(
    ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral' });
      return mermaid;
    },
    (error: unknown) => {
      loading = undefined; // 网络抖一下没加载上：下一张图再试，不让整页的图都画不出来
      throw error;
    },
  );
  return loading;
}

// 依次认：已加引号的一段（跳过）、[…]、(…)、{…}，各自找配对的闭合符，[开始(1)] 里的圆括号不会截断方括号
const NODE_TEXT = /"[^"\n]*"|\[([^\]\n]*)\]|\(([^)\n]*)\)|\{([^}\n]*)\}/g;

/** 节点文字与子图标题加上双引号；已经有引号的不动，文字里的双引号换成单引号。 */
export function quoteLabels(code: string): string {
  const text = code
    .replace(/\u2013/g, '-')
    .replace(/\u00a0/g, ' ')
    .replace(/\r/g, '');
  const titled = text.replace(/^(\s*subgraph\s+)([^"\n]+)$/gm, (_match, head: string, title: string) =>
    title.trim() ? `${head}"${title.trim()}"` : `${head}${title}`,
  );
  return titled.replace(NODE_TEXT, (match: string, square?: string, round?: string, curly?: string) => {
    const inner = square ?? round ?? curly;
    if (inner === undefined) return match;
    const value = inner.trim();
    if (value.startsWith('"') && value.endsWith('"')) return match;
    return `${match[0]}"${value.replace(/"/g, "'")}"${match[match.length - 1]}`;
  });
}

/** 画成 SVG；语法不对（补了引号也不对）或库没加载上就抛错。 */
export async function renderMermaid(code: string): Promise<string> {
  const mermaid = await load();
  for (const attempt of [code, quoteLabels(code)]) {
    if (await mermaid.parse(attempt, { suppressErrors: true })) {
      counter += 1;
      const { svg } = await mermaid.render(`kw-mermaid-${counter}`, attempt);
      return svg;
    }
  }
  throw new Error('mermaid 语法有误');
}
