/**
 * 表格预览：SheetJS 读工作簿，每个工作表一个标签页，前 MAX_ROWS 行照电子表格的样子显示（列名 A、B、C，
 * 行号在左，细网格线）；公式显示文件里存的计算结果。
 */
import { useMemo } from 'react';
import { Empty } from '../../ui/Empty';
import { Tabs } from '../../ui/Tabs';
import { MAX_ROWS, parseSheets, type Sheet } from './sheets';

function columnName(index: number): string {
  let name = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26))
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  return name;
}

function SheetTable({ sheet }: { readonly sheet: Sheet }) {
  const width = Math.max(0, ...sheet.rows.map((row) => row.length));
  if (width === 0) return <Empty description="这个工作表是空的" />;
  const columns = Array.from({ length: width }, (_, i) => columnName(i));
  return (
    <div className="kw-sheet">
      {sheet.total > MAX_ROWS ? (
        <p className="kw-sheet-note">
          共 {sheet.total} 行，这里只显示前 {MAX_ROWS} 行，完整内容请下载
        </p>
      ) : null}
      <div className="kw-sheet-scroll">
        <table className="kw-sheet-table">
          <thead>
            <tr>
              <th aria-label="行号" />
              {columns.map((name) => (
                <th key={name} scope="col">
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.rows.map((row, r) => (
              <tr key={r}>
                <th scope="row">{r + 1}</th>
                {columns.map((name, c) => {
                  const value = row[c];
                  const text = value === null || value === undefined ? '' : String(value);
                  return (
                    <td key={name} title={text}>
                      {text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SheetView({ data }: { readonly data: ArrayBuffer }) {
  const sheets = useMemo(() => {
    try {
      return parseSheets(data);
    } catch {
      return undefined;
    }
  }, [data]);
  if (!sheets)
    return (
      <p className="kw-preview-problem" role="alert">
        这个表格打不开，请下载后查看
      </p>
    );
  if (sheets.length === 1) return <SheetTable sheet={sheets[0]!} />;
  return (
    <Tabs
      items={sheets.map((sheet) => ({
        key: sheet.name,
        label: sheet.name,
        children: <SheetTable sheet={sheet} />,
      }))}
    />
  );
}
