/**
 * 管理页的数据表格。组件库没有表格，照 ChatGPT 企业管理台的列表：表头说明色小字、行间一道发丝线、
 * 悬停一层浅灰；分页在右下角，带「共 N 条」（界面规范第 3 节）。数据都在前端，分页也在前端做。
 */
import { useState, type CSSProperties, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronLeft, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

export interface Column<T> {
  readonly key: string;
  readonly title: ReactNode;
  readonly render: (row: T, index: number) => ReactNode;
  readonly width?: number | string;
  readonly align?: 'left' | 'right' | 'center';
  /** 单行显示，超出部分省略。名称列都开（界面规范 3.3）。 */
  readonly ellipsis?: boolean;
}

interface Props<T> {
  readonly columns: readonly Column<T>[];
  readonly rows: readonly T[];
  readonly rowKey: (row: T) => string;
  /** 后台刷新中：表格变淡，不清空。首屏加载用 Skeleton。 */
  readonly loading?: boolean;
  readonly empty?: ReactNode;
  /** 每页条数；false 表示不分页。 */
  readonly pageSize?: number | false;
  /** 列多时的最小宽度，窄屏横向滚动而不是把中文挤成竖排。 */
  readonly minWidth?: number;
  readonly onRowClick?: (row: T) => void;
  /** 表尾的合计行（<tr>）。 */
  readonly summary?: ReactNode;
  readonly 'aria-label'?: string;
  readonly className?: string;
}

export function Table<T>({
  columns,
  rows,
  rowKey,
  loading,
  empty,
  pageSize = 10,
  minWidth,
  onRowClick,
  summary,
  className,
  ...rest
}: Props<T>) {
  const [current, setCurrent] = useState(1);
  const pages = pageSize ? Math.max(1, Math.ceil(rows.length / pageSize)) : 1;
  // 搜索、删除后总页数变少时停在最后一页，不出现空页
  const page = Math.min(current, pages);
  const shown = pageSize ? rows.slice((page - 1) * pageSize, page * pageSize) : rows;
  const cellStyle = (column: Column<T>): CSSProperties => ({ width: column.width, textAlign: column.align });
  return (
    <div className={cx('kw-table-wrap', className)}>
      <div className="kw-table-scroll">
        <table
          className={cx('kw-table', loading && 'is-loading')}
          style={{ minWidth }}
          aria-label={rest['aria-label']}
        >
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} scope="col" style={cellStyle(column)}>
                  {column.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr className="kw-table-empty">
                <td colSpan={columns.length}>{empty ?? '暂无数据'}</td>
              </tr>
            ) : (
              shown.map((row, index) => (
                <tr
                  key={rowKey(row)}
                  className={onRowClick ? 'is-clickable' : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      style={cellStyle(column)}
                      className={column.ellipsis ? 'is-ellipsis' : undefined}
                    >
                      {column.render(row, index)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
          {summary ? <tfoot>{summary}</tfoot> : null}
        </table>
      </div>
      {pageSize ? <Pager page={page} pages={pages} total={rows.length} onChange={setCurrent} /> : null}
    </div>
  );
}

function pageWindow(page: number, pages: number): readonly (number | 'gap')[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const middle = [page - 1, page, page + 1].filter((p) => p > 1 && p < pages);
  const items: (number | 'gap')[] = [1];
  if ((middle[0] ?? pages) > 2) items.push('gap');
  items.push(...middle);
  if ((middle[middle.length - 1] ?? 1) < pages - 1) items.push('gap');
  items.push(pages);
  return items;
}

function Pager({
  page,
  pages,
  total,
  onChange,
}: {
  readonly page: number;
  readonly pages: number;
  readonly total: number;
  readonly onChange: (page: number) => void;
}) {
  return (
    <nav className="kw-pager" aria-label="分页">
      <span className="kw-pager-total">共 {total} 条</span>
      {pages > 1 ? (
        <>
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            disabled={page <= 1}
            aria-label="上一页"
            onClick={() => onChange(page - 1)}
          >
            <ChevronLeft />
          </Button>
          {pageWindow(page, pages).map((item, i) =>
            item === 'gap' ? (
              <span key={`gap-${i}`} className="kw-pager-gap">
                …
              </span>
            ) : (
              <Button
                key={item}
                color="secondary"
                variant={item === page ? 'soft' : 'ghost'}
                size="sm"
                uniform
                aria-current={item === page ? 'page' : undefined}
                onClick={() => onChange(item)}
              >
                {item}
              </Button>
            ),
          )}
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            disabled={page >= pages}
            aria-label="下一页"
            onClick={() => onChange(page + 1)}
          >
            <ChevronRight />
          </Button>
        </>
      ) : null}
    </nav>
  );
}
