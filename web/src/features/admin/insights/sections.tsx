/** 问答大盘下半部分：需要关注（失败、取消、被点踩、检索了没引用）、工具与知识库两个维度。数字都链到运行记录。 */
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import {
  runsLink,
  type BaseUse,
  type InsightCount,
  type InsightWindow,
  type Insights,
  type ToolUse,
} from '../../../api/insights';
import { slotItems } from '../../../slots/registry';
import { Table, type Column } from '../../../ui/Table';
import { categoryLabel, number, percent, reasonLabel, share } from './measures';

/** 一个数：不是 0 就链到运行记录里对应的那些轮。 */
function Drill({
  count,
  to,
  unit = '轮',
}: {
  readonly count: number;
  readonly to: string;
  readonly unit?: string;
}) {
  if (count === 0) return <span className="kw-secondary">0 {unit}</span>;
  return (
    <Link className="kw-num" to={to}>
      {number(count)} {unit}
    </Link>
  );
}

const breakdown = (items: readonly InsightCount[], label: (key: string) => string): string =>
  items.map((item) => `${label(item.key)} ${number(item.count)}`).join(' · ');

/** 一项需要关注的：名字、轮数（链到运行记录）、占比（写明占哪个总数）、细分。 */
function Concern({
  title,
  children,
  ratio,
  detail,
}: {
  readonly title: string;
  readonly children: ReactNode;
  readonly ratio: string;
  readonly detail?: string;
}) {
  return (
    <li className="kw-insights-concern">
      <span className="kw-insights-concern-title">{title}</span>
      <span>{children}</span>
      <span className="kw-num">{ratio}</span>
      <span className="kw-secondary">{detail}</span>
    </li>
  );
}

/** 「占全部提问 6.9%」；分母为 0 时写「—」。 */
const ratioOf = (part: number, whole: number, of: string): string => {
  const value = share(part, whole);
  return value === null ? '—' : `占${of} ${value}`;
};

interface ConcernItem {
  readonly title: string;
  readonly count: number;
  /** 下钻运行记录用的筛选条件 */
  readonly filter: Readonly<Record<string, string>>;
  readonly ratio: string;
  readonly detail: string;
}

export function Concerns({ data }: { readonly data: Insights }) {
  const { totals } = data;
  const ofAll = (part: number) => ratioOf(part, totals.turns, '全部提问');
  const reasons = (status: string) => data.endReasons.filter((item) => item.status === status);
  const all: readonly ConcernItem[] = [
    {
      title: '失败',
      count: totals.failed,
      filter: { status: 'failed' },
      ratio: ofAll(totals.failed),
      detail: breakdown(reasons('failed'), reasonLabel),
    },
    {
      title: '取消',
      count: totals.cancelled,
      filter: { status: 'cancelled' },
      ratio: ofAll(totals.cancelled),
      detail: breakdown(reasons('cancelled'), reasonLabel),
    },
    {
      title: '被点踩',
      count: totals.disliked,
      filter: { signal: 'disliked' },
      ratio: ofAll(totals.disliked),
      detail: breakdown(data.dislikes, categoryLabel),
    },
    {
      title: '检索了知识库却没引用',
      count: data.uncited,
      filter: { signal: 'uncited' },
      ratio: ratioOf(data.uncited, totals.searched, '检索过知识库的'),
      detail: '答完了，但回答里没有引用检索到的内容',
    },
  ];
  const items = all.filter((item) => item.count > 0); // 是 0 的不用关注，不列
  if (items.length === 0) return <p className="kw-secondary">这段时间没有需要关注的轮次</p>;
  return (
    <ul className="kw-insights-concerns" aria-label="需要关注">
      {items.map((item) => (
        <Concern key={item.title} title={item.title} ratio={item.ratio} detail={item.detail}>
          <Drill count={item.count} to={runsLink(data, item.filter)} />
        </Concern>
      ))}
    </ul>
  );
}

export function ToolTable({
  tools,
  window,
}: {
  readonly tools: readonly ToolUse[];
  readonly window: InsightWindow;
}) {
  const columns: readonly Column<ToolUse>[] = [
    { key: 'name', title: '工具', ellipsis: true, render: (row) => <code>{row.name}</code> },
    {
      key: 'turns',
      title: '用到的轮数',
      width: 120,
      align: 'right',
      render: (row) => <Drill count={row.turns} to={runsLink(window, { tool: row.name })} />,
    },
    {
      key: 'failed',
      title: '其中失败',
      width: 150,
      align: 'right',
      render: (row) => (
        <span>
          <Drill count={row.failed} to={runsLink(window, { tool: row.name, status: 'failed' })} />
          {row.failed > 0 ? <span className="kw-secondary"> · {share(row.failed, row.turns)}</span> : null}
        </span>
      ),
    },
  ];
  return (
    <Table<ToolUse>
      aria-label="工具"
      rowKey={(row) => row.name}
      columns={columns}
      rows={tools}
      pageSize={false}
      empty="这段时间没有调用工具"
    />
  );
}

export function BaseTable({
  bases,
  window,
}: {
  readonly bases: readonly BaseUse[];
  readonly window: InsightWindow;
}) {
  const filter = (row: BaseUse) => ({ base: row.id, baseName: row.name });
  const actions = slotItems('insights.base.action');
  /** 扩展给这个知识源挂的动作（例如去这个库的改进页）；全局 id 是 <知识源id>.<知识库id> */
  const action = (row: BaseUse) => {
    const dot = row.id.indexOf('.');
    const found = actions.find((item) => item.sourceId === row.id.slice(0, dot));
    return found && dot > 0 ? <Link to={found.to(row.id.slice(dot + 1))}>{found.label}</Link> : null;
  };
  const columns: readonly Column<BaseUse>[] = [
    {
      key: 'name',
      title: '知识库',
      ellipsis: true,
      render: (row) => (
        <span className="kw-cell-stack">
          <span>{row.name}</span>
          {action(row)}
        </span>
      ),
    },
    {
      key: 'turns',
      title: '检索到的轮数',
      width: 120,
      align: 'right',
      render: (row) => <Drill count={row.turns} to={runsLink(window, filter(row))} />,
    },
    {
      key: 'cited',
      title: '引用率',
      width: 90,
      align: 'right',
      render: (row) => (
        <span className="kw-num">{percent(row.turns > 0 ? row.cited / row.turns : null)}</span>
      ),
    },
    {
      key: 'unused',
      title: '检索到没用上',
      width: 120,
      align: 'right',
      render: (row) => <span className="kw-num">{number(row.unused)} 轮</span>,
    },
    {
      key: 'disliked',
      title: '被点踩',
      width: 100,
      align: 'right',
      render: (row) => (
        <Drill count={row.disliked} to={runsLink(window, { ...filter(row), signal: 'disliked' })} />
      ),
    },
  ];
  return (
    <Table<BaseUse>
      aria-label="知识库"
      rowKey={(row) => row.id}
      columns={columns}
      rows={bases}
      pageSize={false}
      empty="这段时间没有检索知识库"
    />
  );
}
