/** 用 SheetJS 读工作簿：每个工作表取前 MAX_ROWS 行（二维数组），并记下总行数。 */
import { read, utils } from 'xlsx';

export const MAX_ROWS = 500;

export interface Sheet {
  readonly name: string;
  readonly rows: readonly (readonly unknown[])[];
  readonly total: number;
}

export function parseSheets(data: ArrayBuffer): readonly Sheet[] {
  const book = read(new Uint8Array(data), { type: 'array', dense: true });
  return book.SheetNames.map((name) => {
    const all = utils.sheet_to_json<unknown[]>(book.Sheets[name]!, {
      header: 1,
      blankrows: false,
      defval: '',
    });
    return { name, rows: all.slice(0, MAX_ROWS), total: all.length };
  });
}
