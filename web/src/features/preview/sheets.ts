/**
 * 用 SheetJS 读工作簿：每个工作表从已用区域（!ref）的左上角起取二维数组，空行也保留，
 * 屏幕上的行号、列名和 Excel 里一致；知识库的引用按「工作表 + 行号」定位到这里。
 */
import { read, utils, type WorkSheet } from 'xlsx';

export interface Sheet {
  readonly name: string;
  readonly rows: readonly (readonly unknown[])[];
  /** 第一行在 Excel 里的行号（从 1 起）：已用区域不一定从 A1 开始 */
  readonly firstRow: number;
  /** 第一列在 Excel 里的列序（从 0 起，0 是 A） */
  readonly firstColumn: number;
}

function origin(sheet: WorkSheet): { readonly row: number; readonly column: number } {
  const ref = sheet['!ref'];
  if (!ref) return { row: 1, column: 0 };
  const start = utils.decode_range(ref).s;
  return { row: start.r + 1, column: start.c };
}

export function parseSheets(data: ArrayBuffer): readonly Sheet[] {
  const book = read(new Uint8Array(data), { type: 'array', dense: true });
  return book.SheetNames.map((name) => {
    const sheet = book.Sheets[name]!;
    const rows = utils.sheet_to_json<unknown[]>(sheet, { header: 1, blankrows: true, defval: '' });
    const start = origin(sheet);
    return { name, rows, firstRow: start.row, firstColumn: start.column };
  });
}

/** 列序转 Excel 列名：0 → A，25 → Z，26 → AA。 */
export function columnName(index: number): string {
  let name = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26))
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  return name;
}

/** 样式里定的行高；实际高度渲染后再量一次（折叠边框下可能差一两像素，几万行累积起来定位会偏）。 */
export const ROW_HEIGHT = 28;
const OVERSCAN = 20;

/** 只渲染看得见的那些行：几万行的表也不会把页面卡住。返回要渲染的行区间（左闭右开）。 */
export function visibleRows(
  total: number,
  scrollTop: number,
  viewport: number,
  rowHeight: number = ROW_HEIGHT,
): { readonly first: number; readonly last: number } {
  const first = Math.min(total, Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN));
  const last = Math.min(total, Math.ceil((scrollTop + viewport) / rowHeight) + OVERSCAN);
  return { first, last: Math.max(first, last) };
}
