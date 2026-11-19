/**
 * 编辑器里「AI 修改」交给对话的那句话：点名文件（@路径，照官方 @file 语法，带空格的加引号）、写明「在编辑器里修改」和选中的
 * 是第几块（编辑器工具据此直接在那几块里查找替换，不用先读全文；没有编辑器工具时 Word 技能据此直接在原文件上写修订）、
 * 引用选中的原文、再接用户的要求。用户在对话里看到的就是这句，模型拿到的也是这句，不藏额外指令。
 * 位置按编辑器写：Word 写第几块（编辑器的块号，从 0 数），表格写工作表和区域，演示写第几页（从 1 数）和元素编号。
 */
import { create } from 'zustand';

/** 引用的原文最多留多少字（编辑器给的选区最多 4000 字）：太长的模型照路径自己去读 */
const QUOTE_MAX = 2000;

/** 官方 @file 的写法：路径里有空白就加引号；路径本身带引号写不成 @，退回原样文字 */
export function fileMention(path: string): string {
  if (!/\s/.test(path)) return `@${path}`;
  return path.includes('"') ? path : `@"${path}"`;
}

/** 选区在编辑器里的位置（三种编辑器各一种，见 editorBridge.ts） */
export interface EditLocation {
  readonly blocks?: { readonly start: number; readonly end: number };
  readonly cells?: { readonly sheet: string; readonly range: string };
  readonly slide?: { readonly index: number; readonly elements: readonly string[] };
}

/** 「选中的这段（第 3 块）」这类说法；没有位置时是 null（只说文件） */
function placeOf(where: EditLocation): string | null {
  const { blocks, cells, slide } = where;
  if (cells) return `选中的区域（工作表「${cells.sheet}」${cells.range}）`;
  if (slide) {
    const page = `第 ${slide.index + 1} 页`;
    return slide.elements.length > 0 ? `${page}选中的元素（${slide.elements.join('、')}）` : page;
  }
  if (blocks)
    return blocks.start === blocks.end
      ? `选中的这段（第 ${blocks.start} 块）`
      : `选中的这段（第 ${blocks.start}～${blocks.end} 块）`;
  return null;
}

export function editRequestText(
  path: string,
  selection: string,
  instruction: string,
  where: EditLocation = {},
): string {
  const head = `在编辑器里修改 ${fileMention(path)}`;
  const quoted = selection.trim();
  const place = placeOf(where) ?? (quoted ? '选中的这段' : null);
  if (!place) return `${head}：${instruction}`;
  if (!quoted) return `${head}，${place}：${instruction}`;
  const clipped = quoted.length > QUOTE_MAX ? `${quoted.slice(0, QUOTE_MAX)}…` : quoted;
  const lines = clipped.split('\n').map((line) => `> ${line}`);
  return [`${head}，${place}：`, ...lines, '', instruction].join('\n');
}

/** 别处交给输入框的一句话：输入框按对话认领；send 为真且输入框是空的就直接发出，否则放进输入框 */
export interface ComposerRequest {
  readonly id: number;
  readonly conversationId: string;
  readonly text: string;
  readonly send: boolean;
}

interface ComposerInbox {
  readonly request: ComposerRequest | null;
  readonly offer: (conversationId: string, text: string, send: boolean) => void;
  /** 认领了就清掉（同一句不认领两次） */
  readonly take: (id: number) => void;
}

let nextId = 1;

export const useComposerInbox = create<ComposerInbox>((set) => ({
  request: null,
  offer: (conversationId, text, send) => set({ request: { id: nextId++, conversationId, text, send } }),
  take: (id) => set((state) => (state.request?.id === id ? { request: null } : state)),
}));
