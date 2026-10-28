/**
 * 表格预览：SheetJS 读工作簿，每个工作表一个标签页，照电子表格的样子显示（列名 A、B、C，行号在左，细网格线）；
 * 公式显示文件里存的计算结果。行号、列名和 Excel 一致（空行也保留），整张表都能看，只渲染滚动到的那些行。
 * 传了定位时切到对应工作表、滚到那几行并标出来。
 */
import { TableFilled } from '@openai/apps-sdk-ui/components/Icon';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Empty } from '../../ui/Empty';
import { Tabs } from '../../ui/Tabs';
import { cx } from '../../ui/cx';
import type { SheetLocate } from './locate';
import { columnName, parseSheets, ROW_HEIGHT, visibleRows, type Sheet } from './sheets';

// jsdom 与首帧拿不到容器高度时按这么高算可见区域
const FALLBACK_VIEWPORT = 640;

function Row({
  sheet,
  index,
  width,
  hit,
}: {
  readonly sheet: Sheet;
  readonly index: number;
  readonly width: number;
  readonly hit: boolean;
}) {
  const row = sheet.rows[index]!;
  return (
    <tr className={cx(hit && 'is-hit')} aria-current={hit ? 'true' : undefined}>
      <th scope="row">{sheet.firstRow + index}</th>
      {Array.from({ length: width }, (_, c) => {
        const value = row[c];
        const text = value === null || value === undefined ? '' : String(value);
        return (
          <td key={c} title={text}>
            {text}
          </td>
        );
      })}
    </tr>
  );
}

function SheetTable({ sheet, focus }: { readonly sheet: Sheet; readonly focus?: SheetLocate }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(FALLBACK_VIEWPORT);
  const [rowHeight, setRowHeight] = useState(ROW_HEIGHT);
  // 不用 Math.max(...rows)：几万行展开成参数会超出调用栈上限
  const width = useMemo(() => sheet.rows.reduce((max, row) => Math.max(max, row.length), 0), [sheet]);
  const hitFrom = focus?.rowStart !== undefined ? focus.rowStart - sheet.firstRow : undefined;
  const hitTo = hitFrom !== undefined ? (focus?.rowEnd ?? focus?.rowStart ?? 0) - sheet.firstRow : undefined;

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    if (element.clientHeight > 0) setViewport(element.clientHeight);
    const measured =
      element.querySelector('tbody tr:not([aria-hidden])')?.getBoundingClientRect().height ?? 0;
    const height = measured > 0 ? measured : ROW_HEIGHT;
    setRowHeight(height);
    if (hitFrom !== undefined && hitFrom > 0) {
      // 被引用的行放在可见区域上方三分之一处，上面留几行上下文
      const top = Math.max(0, hitFrom * height - Math.floor(element.clientHeight / 3));
      element.scrollTop = top;
      setScrollTop(top);
    }
  }, [hitFrom]);

  if (width === 0) return <Empty area icon={<TableFilled />} description="这个工作表是空的" />;
  const { first, last } = visibleRows(sheet.rows.length, scrollTop, viewport, rowHeight);
  return (
    <div
      ref={scroller}
      className="kw-sheet-scroll"
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
    >
      <table className="kw-sheet-table">
        <thead>
          <tr>
            <th aria-label="行号" />
            {Array.from({ length: width }, (_, i) => (
              <th key={i} scope="col">
                {columnName(sheet.firstColumn + i)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {first > 0 ? <tr aria-hidden="true" style={{ height: first * rowHeight }} /> : null}
          {Array.from({ length: last - first }, (_, k) => {
            const index = first + k;
            const hit = hitFrom !== undefined && hitTo !== undefined && index >= hitFrom && index <= hitTo;
            return <Row key={index} sheet={sheet} index={index} width={width} hit={hit} />;
          })}
          {last < sheet.rows.length ? (
            <tr aria-hidden="true" style={{ height: (sheet.rows.length - last) * rowHeight }} />
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

export default function SheetView({
  data,
  focus,
}: {
  readonly data: ArrayBuffer;
  readonly focus?: SheetLocate;
}) {
  const sheets = useMemo(() => {
    try {
      return parseSheets(data);
    } catch {
      return undefined;
    }
  }, [data]);
  const target = sheets?.some((sheet) => sheet.name === focus?.sheet) ? focus : undefined;
  const [active, setActive] = useState(target?.sheet);
  // 定位换了就切到它的工作表（知识库的文档页里每点一块都会换定位）；渲染时调整，不另起 effect
  const [followed, setFollowed] = useState(target);
  if (target !== followed) {
    setFollowed(target);
    if (target) setActive(target.sheet);
  }
  if (!sheets)
    return (
      <p className="kw-preview-problem" role="alert">
        这个表格打不开，请下载后查看
      </p>
    );
  if (sheets.length === 1) return <SheetTable sheet={sheets[0]!} focus={target} />;
  return (
    <Tabs
      value={active ?? sheets[0]!.name}
      onChange={setActive}
      items={sheets.map((sheet) => ({
        key: sheet.name,
        label: sheet.name,
        children: <SheetTable sheet={sheet} focus={sheet.name === target?.sheet ? target : undefined} />,
      }))}
    />
  );
}
