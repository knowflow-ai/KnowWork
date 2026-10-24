import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import { utils, write } from 'xlsx';
import { renderWithApp } from '../../test/render';
import SheetView from './SheetView';
import { columnName, parseSheets, visibleRows } from './sheets';

function workbook(
  sheets: Record<string, { readonly rows: unknown[][]; readonly origin?: string }>,
): ArrayBuffer {
  const book = utils.book_new();
  for (const [name, { rows, origin }] of Object.entries(sheets)) {
    const sheet = origin
      ? utils.sheet_add_aoa(utils.aoa_to_sheet([]), rows, { origin })
      : utils.aoa_to_sheet(rows);
    if (origin) {
      // Excel 存的已用区域从第一个有内容的格子算起；SheetJS 按 origin 写时会把它记成从 A1 开始，这里照 Excel 改回来
      const start = utils.decode_cell(origin);
      const range = utils.decode_range(sheet['!ref']!);
      sheet['!ref'] = utils.encode_range({ s: start, e: range.e });
    }
    utils.book_append_sheet(book, sheet, name);
  }
  return write(book, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer;
}

function rowNumbers(): number[] {
  return within(screen.getByRole('table'))
    .getAllByRole('rowheader')
    .map((cell) => Number(cell.textContent));
}

describe('表格预览的行号与列名和 Excel 一致', () => {
  it('已用区域不从 A1 开始时，行号列名照 Excel 算；中间的空行保留', () => {
    const [sheet] = parseSheets(workbook({ 明细: { rows: [['区域'], [], ['华东']], origin: 'C3' } }));
    expect([sheet!.firstRow, sheet!.firstColumn, sheet!.rows.length]).toEqual([3, 2, 3]);
    renderWithApp(<SheetView data={workbook({ 明细: { rows: [['区域'], [], ['华东']], origin: 'C3' } })} />);
    expect(screen.getByRole('columnheader', { name: 'C' })).toBeInTheDocument();
    expect(rowNumbers()).toEqual([3, 4, 5]);
  });

  it('列名进位：Z 之后是 AA', () => {
    expect([0, 25, 26, 27, 701, 702].map(columnName)).toEqual(['A', 'Z', 'AA', 'AB', 'ZZ', 'AAA']);
  });
});

describe('大表只渲染看得见的行', () => {
  it('可见区间按滚动位置算，前后多留一些', () => {
    expect(visibleRows(100_000, 0, 560, 28)).toEqual({ first: 0, last: 40 });
    expect(visibleRows(100_000, 28 * 5000, 560, 28)).toEqual({ first: 4980, last: 5040 });
    expect(visibleRows(30, 0, 560, 28)).toEqual({ first: 0, last: 30 });
  });

  it('两万行的表不会全部进 DOM，也没有「只显示前几百行」的截断提示', () => {
    const rows = Array.from({ length: 20_000 }, (_, i) => [`第 ${i + 1} 行`]);
    renderWithApp(<SheetView data={workbook({ 明细: { rows } })} />);
    expect(rowNumbers().length).toBeLessThan(200);
    expect(screen.queryByText(/只显示前/)).not.toBeInTheDocument();
  });
});

describe('按引用定位', () => {
  it('切到引用所在的工作表，标出引用的那几行', () => {
    const data = workbook({
      汇总: { rows: [['区域', '销售额']] },
      明细: { rows: Array.from({ length: 3000 }, (_, i) => [`订单 ${i + 1}`]) },
    });
    renderWithApp(
      <SheetView data={data} focus={{ type: 'sheet', sheet: '明细', rowStart: 2000, rowEnd: 2001 }} />,
    );
    expect(screen.getByRole('tab', { name: '明细' })).toHaveAttribute('aria-selected', 'true');
    const hits = within(screen.getByRole('table'))
      .getAllByRole('row')
      .filter((row) => row.getAttribute('aria-current') === 'true');
    expect(hits.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['2000', '2001']);
  });

  it('定位换到别的工作表时跟过去（知识库文档页里每点一块都会换）', () => {
    const data = workbook({ 汇总: { rows: [['区域'], ['华东']] }, 明细: { rows: [['订单'], ['订单 1']] } });
    const { rerender } = renderWithApp(
      <SheetView data={data} focus={{ type: 'sheet', sheet: '汇总', rowStart: 2, rowEnd: 2 }} />,
    );
    expect(screen.getByRole('tab', { name: '汇总' })).toHaveAttribute('aria-selected', 'true');
    rerender(<SheetView data={data} focus={{ type: 'sheet', sheet: '明细', rowStart: 2, rowEnd: 2 }} />);
    expect(screen.getByRole('tab', { name: '明细' })).toHaveAttribute('aria-selected', 'true');
  });

  it('工作表名对不上时照常打开第一个工作表', () => {
    const data = workbook({ 汇总: { rows: [['区域']] }, 明细: { rows: [['订单']] } });
    renderWithApp(<SheetView data={data} focus={{ type: 'sheet', sheet: '不存在', rowStart: 1 }} />);
    expect(screen.getByRole('tab', { name: '汇总' })).toHaveAttribute('aria-selected', 'true');
  });
});
