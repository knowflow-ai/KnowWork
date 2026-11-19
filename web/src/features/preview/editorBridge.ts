/**
 * 抽屉里的在线编辑器（GenOffice，嵌在 iframe 里、在另一个来源）和外层页面之间的消息（GenOffice 补丁 6 的格式）：
 * - 编辑器发来 `genoffice:saved`：这份文件存了盘（保存、自动保存、智能体存盘都算，GenOffice 补丁 2），云端要把它存回对话；
 * - 编辑器发来 `genoffice:ai-edit`：用户在编辑器里选中一段、写了要求点「AI 修改」（或编辑器自带的校对、翻译这类 AI 功能），
 *   选区与要求交给外层页面的对话；选区按编辑器说位置：Word 是第几块，表格是哪张工作表的哪个区域，演示是第几页的哪几个元素；`genoffice:ready`：编辑器起好了（重新载入后要把「AI 正在改」的标记再发一次）；
 *   `genoffice:disconnected`：编辑服务断了（GenOffice 补丁 14），外层页面重新要地址、重新载入，用户看不到它自己的断线提示；
 * - 外层页面发 `genoffice:ai-busy`：给 AI 正在改的地方加标记（只是装饰，不进文档、不进撤销）：Word 是几块（ranges），表格是
 *   工作表的区域（cells），演示是第几页的几个元素（slide）；什么都不带就收掉。
 * 来的消息先核对形状，不认识的丢掉。
 */

/** 用户在编辑器里交给 AI 的一次修改 */
export interface EditorAiEdit {
  readonly instruction: string;
  /** 编辑器里按了「立即发送」（否则是先放进输入框） */
  readonly send: boolean;
  /** 选中的原文（没选中是空） */
  readonly selection: string;
  /** Word：选中的块范围（从 0 开始，含两端）；只有选中了一段才有 */
  readonly blocks?: { readonly start: number; readonly end: number };
  /** 表格：选中的区域（工作表名、A1 区域） */
  readonly cells?: { readonly sheet: string; readonly range: string };
  /** 演示：第几页（从 0 开始）和选中的元素（持久的元素编号） */
  readonly slide?: { readonly index: number; readonly elements: readonly string[] };
}

export interface BusyRange {
  readonly start: number;
  readonly end: number;
  readonly label?: string;
}

const isBlockIndex = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;

/**
 * 编辑器发来的消息：「AI 修改」交回 EditorAiEdit，编辑器起好了交回 'ready'，断线交回 'disconnected'，存了盘交回 'saved'，
 * AI 的改动落进了文档交回 'agent-edited'，别的都是 null
 */
export function editorMessageOf(
  data: unknown,
): EditorAiEdit | 'ready' | 'disconnected' | 'saved' | 'agent-edited' | null {
  if (typeof data !== 'object' || data === null) return null;
  const message = data as Record<string, unknown>;
  if (message.type === 'genoffice:ready') return 'ready';
  if (message.type === 'genoffice:disconnected') return 'disconnected';
  if (message.type === 'genoffice:saved') return 'saved';
  if (message.type === 'genoffice:agent-edited') return 'agent-edited';
  if (message.type !== 'genoffice:ai-edit') return null;
  const instruction = typeof message.instruction === 'string' ? message.instruction.trim() : '';
  if (!instruction) return null;
  const selection = (
    typeof message.selection === 'object' && message.selection !== null ? message.selection : {}
  ) as Record<string, unknown>;
  const text = typeof selection.text === 'string' ? selection.text : '';
  const cells = cellsOf(selection);
  if (cells) return { instruction, send: message.send === true, selection: '', cells };
  const slide = slideOf(selection);
  if (slide)
    return {
      instruction,
      send: message.send === true,
      selection: slide.elements.length > 0 ? text : '',
      slide,
    };
  const ranged =
    selection.isRange === true &&
    isBlockIndex(selection.startBlockIndex) &&
    isBlockIndex(selection.endBlockIndex) &&
    selection.endBlockIndex >= selection.startBlockIndex;
  return {
    instruction,
    send: message.send === true,
    selection: ranged ? text : '',
    ...(ranged
      ? { blocks: { start: selection.startBlockIndex as number, end: selection.endBlockIndex as number } }
      : {}),
  };
}

/** 表格发来的选区：工作表名和 A1 区域，形状不对就当没有 */
function cellsOf(selection: Record<string, unknown>): EditorAiEdit['cells'] {
  const { sheet, range } = selection;
  if (typeof sheet !== 'string' || typeof range !== 'string') return undefined;
  if (!sheet.trim() || !/^\$?[A-Z]{1,3}\$?\d+(:\$?[A-Z]{1,3}\$?\d+)?$/i.test(range)) return undefined;
  return { sheet, range: range.toUpperCase() };
}

/** 演示发来的选区：第几页和元素编号，形状不对就当没有 */
function slideOf(selection: Record<string, unknown>): EditorAiEdit['slide'] {
  if (!isBlockIndex(selection.slide)) return undefined;
  const elements = Array.isArray(selection.elements)
    ? selection.elements.flatMap((element: unknown) => {
        const id =
          typeof element === 'object' && element !== null ? (element as { id?: unknown }).id : undefined;
        return typeof id === 'string' && id.trim() ? [id] : [];
      })
    : [];
  return { index: selection.slide, elements };
}

/** 编辑器里 AI 正在改的地方（三种编辑器各一种），什么都没有是空闲 */
export interface EditorBusy {
  readonly ranges?: readonly BusyRange[];
  readonly cells?: { readonly sheet: string; readonly range: string };
  readonly slide?: { readonly index: number; readonly elements: readonly string[] };
}

export const IDLE: EditorBusy = {};

export function isIdle(busy: EditorBusy): boolean {
  return !busy.cells && !busy.slide && (busy.ranges?.length ?? 0) === 0;
}

/** 交给对话的这次修改要标在哪：Word 的几块、表格的区域、演示选中的元素；说不出位置就不标 */
export function busyOf(edit: EditorAiEdit, label: string): EditorBusy {
  if (edit.blocks) return { ranges: [{ ...edit.blocks, label }] };
  if (edit.cells) return { cells: edit.cells };
  if (edit.slide && edit.slide.elements.length > 0) return { slide: edit.slide };
  return IDLE;
}

/** 发给编辑器的「AI 正在改」标记（label 是表格、演示的标签；Word 的标签在每一块里） */
export function busyMessage(
  busy: EditorBusy,
  label = '',
): {
  readonly type: 'genoffice:ai-busy';
  readonly ranges: readonly BusyRange[];
  readonly cells?: { readonly sheet: string; readonly range: string; readonly label: string };
  readonly slide?: { readonly index: number; readonly elements: readonly string[] };
  readonly label?: string;
} {
  return {
    type: 'genoffice:ai-busy',
    ranges: busy.ranges ?? [],
    ...(busy.cells ? { cells: { ...busy.cells, label } } : {}),
    ...(busy.slide ? { slide: busy.slide, label } : {}),
  };
}
