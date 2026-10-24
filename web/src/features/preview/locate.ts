/**
 * 预览时要定位高亮的位置：知识库的引用点开、文档页点分块时传进来，产物预览不传、行为不变。
 * 对应后端知识源协议的定位（server/src/knowwork_server/knowledge/models.py 的 Locator），按预览方式归成四种：
 * - pdf：PDF 以及转成 PDF 预览的 PPT。有框按框画；没有框（幻灯片）时按每处原文在文字里找；
 * - docx：Word 在浏览器里直接渲染（DocxView），按块对上的正文段落区间整段高亮；没有区间时按每处原文找；
 * - sheet：表格按工作表与真实行号；
 * - text：纯文本按字符区间，没有区间时按原文找。
 * 一个定位可以是一块的好几处（mergeLocate.ts 合并）：框、区间、原文都是列表。
 */
import { isRecord, num, str } from '../../api/parse';

/** 原文上的一个框：占页面宽高的比例（0–1，左上角为原点）。 */
export interface Box {
  readonly page: number;
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
}

export interface PdfLocate {
  readonly type: 'pdf';
  /** 页码（从 1 起）：没有框时先在这一页找 */
  readonly page?: number;
  readonly boxes: readonly Box[];
  /** 每处的原文：没有框时逐段找、都高亮 */
  readonly quotes: readonly string[];
}

export interface SheetLocate {
  readonly type: 'sheet';
  readonly sheet: string;
  /** Excel 里的真实行号（从 1 起，含表头） */
  readonly rowStart?: number;
  readonly rowEnd?: number;
}

export interface TextLocate {
  readonly type: 'text';
  readonly start?: number;
  readonly end?: number;
  readonly quote: string;
}

/** Word 正文第几个到第几个段落或表格（左闭右开，数法同 docx-preview：段落、表格各算一个）。 */
export interface BodyRange {
  readonly start: number;
  readonly end: number;
}

export interface DocxLocate {
  readonly type: 'docx';
  /** 一块由几个版面块组成时有好几段 */
  readonly ranges: readonly BodyRange[];
  /** 每处的原文：没有区间时（解析时没对上段落的块）按它在渲染出的段落里找 */
  readonly quotes: readonly string[];
}

export type PreviewLocate = PdfLocate | DocxLocate | SheetLocate | TextLocate;

function boxOf(value: unknown): Box | undefined {
  if (!isRecord(value)) return undefined;
  const [page, x0, y0, x1, y1] = [value.page, value.x0, value.y0, value.x1, value.y1].map(num);
  if (page === undefined || x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) {
    return undefined;
  }
  return { page, x0, y0, x1, y1 };
}

/** 把接口里的定位（后端字段名）换成预览用的；认不出的返回 undefined，预览照常打开、不高亮。 */
export function locateOf(value: unknown): PreviewLocate | undefined {
  if (!isRecord(value)) return undefined;
  const quote = str(value.quote) ?? '';
  const quotes = quote ? [quote] : [];
  switch (value.type) {
    case 'docx': {
      const [start, end] = [num(value.start), num(value.end)];
      const ranges = start !== undefined && end !== undefined && end > start ? [{ start, end }] : [];
      return { type: 'docx', ranges, quotes };
    }
    case 'pdf':
    case 'slide': {
      const boxes = Array.isArray(value.boxes)
        ? value.boxes.map(boxOf).filter((box): box is Box => box !== undefined)
        : [];
      return { type: 'pdf', page: num(value.page), boxes, quotes };
    }
    case 'sheet': {
      const sheet = str(value.sheet);
      return sheet
        ? { type: 'sheet', sheet, rowStart: num(value.row_start), rowEnd: num(value.row_end) }
        : undefined;
    }
    case 'text':
      return { type: 'text', start: num(value.start), end: num(value.end), quote };
    default:
      return undefined;
  }
}
