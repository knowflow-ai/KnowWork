/** 审计：谁在什么时候做了什么、结果如何。只读，按人员类型、结果、动作筛选，向下翻页。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { useInfiniteQuery } from '@tanstack/react-query';
import { listAuditLogs, validateAction, type AuditEntry, type AuditFilter } from '../../../api/audit';
import { memberLabel } from '../../../api/workspace';
import { formatDateTime } from '../../../utils/format';
import { AdminPage, Toolbar } from '../AdminPage';
import { useMembers } from '../queries';
import { FilterSelect } from '../../../ui/FilterSelect';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { ACTOR_OPTIONS, actorTypeLabel, RESULT_BADGE, RESULT_OPTIONS } from './labels';

export default function AuditPage() {
  const [filter, setFilter] = useState<AuditFilter>({});
  const [actionInput, setActionInput] = useState('');
  const members = useMembers();
  const logs = useInfiniteQuery({
    queryKey: ['admin', 'audit', filter],
    queryFn: ({ pageParam }) => listAuditLogs(filter, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const rows = (logs.data?.pages ?? []).flatMap((p) => p.items);
  const actorName = (entry: AuditEntry) => {
    const member = members.data?.find((m) => m.userId === entry.actorId);
    return member ? memberLabel(member) : (entry.actorId ?? '—');
  };
  const actionProblem = validateAction(actionInput);

  const columns: readonly Column<AuditEntry>[] = [
    {
      key: 'time',
      title: '时间',
      width: 150,
      render: (e) => <span className="kw-num">{formatDateTime(e.occurredAt)}</span>,
    },
    {
      key: 'actor',
      title: '操作者',
      width: 180,
      ellipsis: true,
      render: (e) => (
        <span className="kw-cell-stack">
          <span>{actorName(e)}</span>
          <span className="kw-secondary">{actorTypeLabel(e.actorType)}</span>
        </span>
      ),
    },
    { key: 'action', title: '动作', width: 200, ellipsis: true, render: (e) => e.action },
    {
      key: 'resource',
      title: '对象',
      ellipsis: true,
      render: (e) => (e.resourceType ? `${e.resourceType}${e.resourceId ? `：${e.resourceId}` : ''}` : '—'),
    },
    {
      key: 'result',
      title: '结果',
      width: 100,
      render: (e) => {
        const badge = RESULT_BADGE[e.result] ?? { status: 'default' as const, text: e.result };
        return <StatusDot tone={badge.status}>{badge.text}</StatusDot>;
      },
    },
    { key: 'ip', title: 'IP', width: 130, ellipsis: true, render: (e) => e.ip ?? '—' },
  ];

  return (
    <AdminPage
      title="审计"
      description="查看谁在什么时候做了哪些操作，包括 Agent 调用的工具与批准的审批。日志保留 90 天。"
    >
      <Toolbar
        left={
          <>
            <FilterSelect
              label="操作者类型"
              clearable
              width={140}
              options={ACTOR_OPTIONS}
              value={filter.actorType}
              onChange={(actorType) => setFilter((f) => ({ ...f, actorType }))}
            />
            <FilterSelect
              label="结果"
              clearable
              width={120}
              options={RESULT_OPTIONS}
              value={filter.result}
              onChange={(result) => setFilter((f) => ({ ...f, result }))}
            />
            <SearchInput
              placeholder="动作，例如 api_key.*，回车筛选"
              aria-label="动作"
              invalid={Boolean(actionProblem)}
              value={actionInput}
              onChange={setActionInput}
              onSearch={(value) =>
                validateAction(value) === null &&
                setFilter((f) => ({ ...f, action: value.trim() || undefined }))
              }
            />
            {actionProblem ? <span className="kw-secondary">{actionProblem}</span> : null}
          </>
        }
      />
      {logs.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <Table<AuditEntry>
            rowKey={(e) => e.id}
            columns={columns}
            rows={rows}
            minWidth={960}
            pageSize={false}
            empty={logs.isError ? '审计日志加载失败，请刷新重试' : '没有符合条件的记录'}
          />
          <div className="kw-admin-more">
            <span className="kw-secondary">已加载 {rows.length} 条</span>
            {logs.hasNextPage ? (
              <Button
                color="secondary"
                variant="outline"
                loading={logs.isFetchingNextPage}
                onClick={() => void logs.fetchNextPage()}
              >
                加载更多
              </Button>
            ) : null}
          </div>
        </>
      )}
    </AdminPage>
  );
}
