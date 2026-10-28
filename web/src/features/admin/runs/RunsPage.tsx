/**
 * 运行记录（运行调试视图的管理端）：全空间每一轮按时间列出，点开看每一步。只有空间管理员能看；
 * 查看、导出别人的运行会记进审计日志（服务端记）。按时间段、成员、状态、信号（检索了没引用、被点踩、没答上来）筛选，向下翻页。
 * 列表读服务端的每轮汇总：每行有提问、检索与引用、赞踩。
 * 筛选条件都在链接参数里：问答大盘点数字跳过来时带上它统计用的时间段（精确到时刻）、工具、知识库，
 * 这几项显示成可以去掉的标签；自己选的时间段、成员、状态、信号同样写回链接，复制链接就是同样的筛选。
 */
import { lazy, Suspense, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { useInfiniteQuery } from '@tanstack/react-query';
import { listRuns, RUN_SIGNALS, workspaceRun, type RunFilter, type RunRow } from '../../../api/debug';
import { memberLabel } from '../../../api/workspace';
import { formatDateTime } from '../../../utils/format';
import { AdminPage, Toolbar } from '../AdminPage';
import { useMembers } from '../queries';
import { FilterSelect } from '../../../ui/FilterSelect';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { duration, statusTone, statusLabel } from '../../debug/format';

const RunInspector = lazy(() => import('../../debug/RunInspector'));

const RANGES = [
  { value: '1', label: '最近一天' },
  { value: '7', label: '最近 7 天' },
  { value: '30', label: '最近 30 天' },
  { value: '90', label: '最近 90 天' },
] as const;

const STATUSES = ['completed', 'running', 'cancelled', 'failed'].map((value) => ({
  value,
  label: statusLabel(value),
}));

const DAY_MS = 24 * 60 * 60 * 1000;

/** 大盘带过来、只能去掉的筛选：标签上的说法，去掉时一起删的参数 */
const CARRIED = [
  {
    keys: ['since', 'until', 'from', 'to'],
    label: (p: URLSearchParams) => `时间段：${p.get('from')} 至 ${p.get('to')}`,
    shown: (p: URLSearchParams) => p.has('since'),
  },
  {
    keys: ['tool'],
    label: (p: URLSearchParams) => `工具：${p.get('tool')}`,
    shown: (p: URLSearchParams) => p.has('tool'),
  },
  {
    keys: ['base', 'baseName'],
    label: (p: URLSearchParams) => `知识库：${p.get('baseName') ?? p.get('base')}`,
    shown: (p: URLSearchParams) => p.has('base'),
  },
] as const;

export default function RunsPage() {
  const members = useMembers();
  const [params, setParams] = useSearchParams();
  const [opened, setOpened] = useState<RunRow>();
  const value = (key: string) => params.get(key) ?? undefined;
  const days = value('days') ?? '7';
  const [user, status] = [value('user'), value('status')];
  // 只认认识的信号：收藏的旧链接里可能是改过名的（missing 改成了 unanswered），原样发出去服务端会拒
  const signal = RUN_SIGNALS.some((s) => s.value === value('signal')) ? value('signal') : undefined;
  const update = (changes: Readonly<Record<string, string | undefined>>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, changed] of Object.entries(changes)) {
          if (changed) next.set(key, changed);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  const drop = (keys: readonly string[]) => update(Object.fromEntries(keys.map((key) => [key, undefined])));
  // 时间段在取数时才按当下往前推（渲染时不读时钟）；大盘带过来的是确定的起止时刻
  const runs = useInfiniteQuery({
    queryKey: ['admin', 'runs', params.toString()],
    queryFn: ({ pageParam }) => {
      const filter: RunFilter = {
        since: value('since') ?? new Date(Date.now() - Number(days) * DAY_MS).toISOString(),
        until: value('until'),
        user,
        status,
        signal,
        tool: value('tool'),
        base: value('base'),
      };
      return listRuns(filter, pageParam);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const rows = (runs.data?.pages ?? []).flatMap((page) => page.items);
  const memberName = (row: RunRow) => {
    const member = members.data?.find((m) => m.userId === row.userId);
    return member ? memberLabel(member) : (row.userName ?? row.userId);
  };

  const columns: readonly Column<RunRow>[] = [
    {
      key: 'started',
      title: '开始时间',
      width: 150,
      render: (row) => <span className="kw-num">{formatDateTime(row.startedAt)}</span>,
    },
    { key: 'user', title: '成员', width: 160, ellipsis: true, render: (row) => memberName(row) },
    {
      key: 'question',
      title: '提问',
      ellipsis: true,
      render: (row) => (
        <span className="kw-cell-stack">
          <span>{row.question || row.title || '未命名会话'}</span>
          <span className="kw-secondary">
            {row.title || '未命名会话'} · 第 {row.turn} 轮
          </span>
        </span>
      ),
    },
    {
      key: 'status',
      title: '状态',
      width: 100,
      render: (row) => <StatusDot tone={statusTone(row.status)}>{statusLabel(row.status)}</StatusDot>,
    },
    { key: 'duration', title: '耗时', width: 110, render: (row) => duration(row.durationMs) },
    {
      key: 'knowledge',
      title: '知识库',
      width: 150,
      render: (row) =>
        row.searches === null ? (
          '—'
        ) : (
          <span className="kw-num">
            检索 {row.searches} 次 · 引用 {row.cited ?? 0} 条
          </span>
        ),
    },
    {
      key: 'rating',
      title: '反馈',
      width: 70,
      render: (row) => (row.rating === 'like' ? '赞' : row.rating === 'dislike' ? '踩' : '—'),
    },
    { key: 'calls', title: '模型调用', width: 90, align: 'right', render: (row) => row.modelCalls },
    {
      key: 'tokens',
      title: 'Token',
      width: 150,
      render: (row) => (
        <span className="kw-num">
          {row.inputTokens} / {row.outputTokens}
        </span>
      ),
    },
    { key: 'cost', title: '费用', width: 90, render: (row) => row.cost ?? '—' },
  ];

  return (
    <AdminPage
      title="运行记录"
      description="全空间每一轮的运行情况，点开逐步查看模型输出、工具调用与失败原因。查看别人的运行会记进审计日志。"
    >
      <Toolbar
        left={
          <>
            {params.has('since') ? null : (
              <FilterSelect
                label="时间段"
                width={140}
                options={RANGES}
                value={days}
                onChange={(next) => update({ days: next ?? '7' })}
              />
            )}
            <FilterSelect
              label="成员"
              clearable
              width={180}
              options={(members.data ?? []).map((m) => ({ value: m.userId, label: memberLabel(m) }))}
              value={user}
              onChange={(next) => update({ user: next })}
            />
            <FilterSelect
              label="状态"
              clearable
              width={120}
              options={STATUSES}
              value={status}
              onChange={(next) => update({ status: next })}
            />
            <FilterSelect
              label="信号"
              clearable
              width={150}
              options={RUN_SIGNALS}
              value={signal}
              onChange={(next) => update({ signal: next })}
            />
            {CARRIED.filter((item) => item.shown(params)).map((item) => (
              <Button
                key={item.keys[0]}
                color="secondary"
                variant="soft"
                size="sm"
                aria-label={`去掉筛选：${item.label(params)}`}
                onClick={() => drop(item.keys)}
              >
                {item.label(params)}
                <X />
              </Button>
            ))}
          </>
        }
      />
      {runs.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <Table<RunRow>
            rowKey={(row) => `${row.conversationId}:${row.turn}`}
            columns={columns}
            rows={rows}
            minWidth={1180}
            pageSize={false}
            onRowClick={setOpened}
            empty={runs.isError ? '运行记录加载失败，请刷新重试' : '这段时间没有运行记录'}
          />
          <div className="kw-admin-more">
            <span className="kw-secondary">已加载 {rows.length} 轮</span>
            {runs.hasNextPage ? (
              <Button
                color="secondary"
                variant="outline"
                loading={runs.isFetchingNextPage}
                onClick={() => void runs.fetchNextPage()}
              >
                加载更多
              </Button>
            ) : null}
          </div>
        </>
      )}
      {opened ? (
        <Suspense fallback={null}>
          <RunInspector
            source={workspaceRun(opened.conversationId)}
            turn={opened.turn}
            title={`${memberName(opened)} · ${opened.title || '未命名会话'} · 第 ${opened.turn} 轮`}
            onClose={() => setOpened(undefined)}
          />
        </Suspense>
      ) : null}
    </AdminPage>
  );
}
