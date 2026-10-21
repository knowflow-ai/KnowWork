/** 用量表：选时间范围与汇总方式，列出调用次数、输入输出 token 与估算费用，底部合计。 */
import { useState } from 'react';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { useQuery } from '@tanstack/react-query';
import type { UsageGroupBy, UsageQuery, UsageRow } from '../../api/models';
import { Skeleton } from '../../ui/Skeleton';
import { Table, type Column } from '../../ui/Table';
import { RANGES, rangeOf, totals, type RangeKey } from './range';

interface Props {
  readonly queryKey: readonly unknown[];
  readonly fetcher: (query: UsageQuery) => Promise<readonly UsageRow[]>;
  readonly groupings: readonly { readonly value: UsageGroupBy; readonly label: string }[];
  /** 把汇总键（用户 id、模型别名、日期）翻成显示名。 */
  readonly labelOf?: (groupBy: UsageGroupBy, key: string) => string;
}

const KEY_TITLE: Record<UsageGroupBy, string> = { user: '成员', model: '模型', day: '日期' };
const numberCell = (n: number) => <span className="kw-num">{n.toLocaleString('zh-CN')}</span>;

export function UsageView({ queryKey, fetcher, groupings, labelOf }: Props) {
  const [range, setRange] = useState<RangeKey>('7d');
  const [groupBy, setGroupBy] = useState<UsageGroupBy>(groupings[0]?.value ?? 'model');
  const dates = rangeOf(range);
  const usage = useQuery({
    queryKey: [...queryKey, range, groupBy, dates.start, dates.end],
    queryFn: () => fetcher({ ...dates, groupBy }),
  });
  const rows = usage.data ?? [];
  const sum = totals(rows);
  const columns: readonly Column<UsageRow>[] = [
    {
      key: 'key',
      title: KEY_TITLE[groupBy],
      ellipsis: true,
      render: (row) => labelOf?.(groupBy, row.key) ?? row.key,
    },
    { key: 'calls', title: '调用次数', width: 110, align: 'right', render: (row) => numberCell(row.calls) },
    {
      key: 'input',
      title: '输入 token',
      width: 130,
      align: 'right',
      render: (row) => numberCell(row.inputTokens),
    },
    {
      key: 'output',
      title: '输出 token',
      width: 130,
      align: 'right',
      render: (row) => numberCell(row.outputTokens),
    },
    {
      key: 'total',
      title: '合计 token',
      width: 130,
      align: 'right',
      render: (row) => numberCell(row.totalTokens),
    },
    {
      key: 'cost',
      title: '估算费用',
      width: 110,
      align: 'right',
      render: (row) => <span className="kw-num">{row.cost.toFixed(2)}</span>,
    },
  ];
  const summary =
    rows.length > 0 ? (
      <tr>
        <td>合计</td>
        <td style={{ textAlign: 'right' }}>{numberCell(sum.calls)}</td>
        <td style={{ textAlign: 'right' }}>{numberCell(sum.inputTokens)}</td>
        <td style={{ textAlign: 'right' }}>{numberCell(sum.outputTokens)}</td>
        <td style={{ textAlign: 'right' }}>{numberCell(sum.totalTokens)}</td>
        <td style={{ textAlign: 'right' }}>
          <span className="kw-num">{sum.cost.toFixed(2)}</span>
        </td>
      </tr>
    ) : undefined;
  return (
    <>
      <div className="kw-admin-toolbar">
        <div className="kw-admin-toolbar-left">
          <SegmentedControl<RangeKey> aria-label="时间范围" value={range} onChange={setRange}>
            {RANGES.map((r) => (
              <SegmentedControl.Option key={r.value} value={r.value}>
                {r.label}
              </SegmentedControl.Option>
            ))}
          </SegmentedControl>
          <span className="kw-secondary">
            {dates.start} 至 {dates.end}
          </span>
        </div>
        <div className="kw-admin-toolbar-right">
          <SegmentedControl<UsageGroupBy> aria-label="汇总方式" value={groupBy} onChange={setGroupBy}>
            {groupings.map((g) => (
              <SegmentedControl.Option key={g.value} value={g.value}>
                {g.label}
              </SegmentedControl.Option>
            ))}
          </SegmentedControl>
        </div>
      </div>
      {usage.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<UsageRow>
          rowKey={(row) => row.key}
          columns={columns}
          rows={rows}
          pageSize={20}
          summary={summary}
          empty={usage.isError ? '用量加载失败，请刷新重试' : '这段时间没有用量'}
        />
      )}
    </>
  );
}
