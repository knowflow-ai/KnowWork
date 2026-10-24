/** 运行面板的任务列表：按状态、池、类型筛选，按创建时间倒序翻页；行内看详情、取消、立即重跑、删除记录。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import {
  cancelJob,
  deleteJob,
  retryJob,
  type JobRow,
  type JobStatus,
  type KindInfo,
} from '../../../api/jobs';
import { formatDateTime } from '../../../utils/format';
import { FilterSelect } from '../../../ui/FilterSelect';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { JobSheet } from './JobSheet';
import { STATUS_OPTIONS, cancelable, kindLabel, poolLabel, removable, retryable, jobState } from './labels';
import { JOBS_KEY, useJobList } from './queries';

interface Props {
  readonly kinds: readonly KindInfo[];
  readonly pools: readonly string[];
}

export function JobList({ kinds, pools }: Props) {
  const [status, setStatus] = useState<JobStatus>();
  const [pool, setPool] = useState<string>();
  const [kind, setKind] = useState<string>();
  const [opened, setOpened] = useState<string>();
  const list = useJobList({ status, pool, kind });
  const rows = (list.data?.pages ?? []).flatMap((page) => page.items);
  const options = { invalidate: [JOBS_KEY] };
  const cancel = useAction(cancelJob, { ...options, success: '已请求取消', failure: '取消没有成功' });
  const retry = useAction(retryJob, { ...options, success: '已重新排队', failure: '重跑没有成功' });
  const remove = useAction(deleteJob, { ...options, success: '记录已删除', failure: '删除没有成功' });

  const columns: readonly Column<JobRow>[] = [
    {
      key: 'created',
      title: '创建时间',
      width: 150,
      render: (row) => <span className="kw-num">{formatDateTime(row.createdAt)}</span>,
    },
    {
      key: 'kind',
      title: '类型',
      width: 180,
      ellipsis: true,
      render: (row) => (
        <span className="kw-cell-stack">
          <span>{kindLabel(kinds, row.kind)}</span>
          <span className="kw-secondary">{poolLabel(row.pool)}</span>
        </span>
      ),
    },
    {
      key: 'subject',
      title: '对象 ID',
      ellipsis: true,
      render: (row) => <span className="kw-num">{row.subjectId}</span>,
    },
    {
      key: 'status',
      title: '状态',
      width: 130,
      render: (row) => {
        const state = jobState(row);
        return (
          <span className="kw-cell-stack">
            <StatusDot tone={state.tone}>{state.label}</StatusDot>
            <span className="kw-secondary">
              第 {row.tries} / {row.maxTries} 次
            </span>
          </span>
        );
      },
    },
    { key: 'stage', title: '阶段', width: 110, ellipsis: true, render: (row) => row.stage ?? '—' },
    { key: 'error', title: '错误码', width: 150, ellipsis: true, render: (row) => row.errorCode ?? '—' },
    {
      key: 'actions',
      title: '操作',
      width: 220,
      render: (row) => (
        <RowActions
          actions={[
            { key: 'detail', label: '详情', onClick: () => setOpened(row.id) },
            ...(cancelable(kinds, row)
              ? [
                  {
                    key: 'cancel',
                    label: '取消任务',
                    confirm: '取消后按处理失败收尾，用户可以重新发起。确定取消？',
                    onClick: () => void cancel.run(row.id),
                  },
                ]
              : []),
            ...(retryable(row)
              ? [{ key: 'retry', label: '立即重跑', onClick: () => void retry.run(row.id) }]
              : []),
            ...(removable(row)
              ? [
                  {
                    key: 'delete',
                    label: '删除记录',
                    danger: true,
                    confirm: '删除这条任务记录？',
                    onClick: () => void remove.run(row.id),
                  },
                ]
              : []),
          ]}
        />
      ),
    },
  ];

  return (
    <>
      <Toolbar
        left={
          <>
            <FilterSelect
              label="状态"
              clearable
              width={130}
              options={STATUS_OPTIONS}
              value={status}
              onChange={setStatus}
            />
            <FilterSelect
              label="任务池"
              clearable
              width={170}
              options={pools.map((value) => ({ value, label: poolLabel(value) }))}
              value={pool}
              onChange={setPool}
            />
            <FilterSelect
              label="类型"
              clearable
              width={180}
              options={kinds.map((item) => ({ value: item.name, label: item.label }))}
              value={kind}
              onChange={setKind}
            />
          </>
        }
      />
      {list.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <Table<JobRow>
            aria-label="任务"
            rowKey={(row) => row.id}
            columns={columns}
            rows={rows}
            loading={list.isFetching && !list.isFetchingNextPage}
            minWidth={1080}
            pageSize={false}
            empty={list.isError ? '任务列表加载失败，请刷新重试' : '没有符合条件的任务'}
          />
          <div className="kw-admin-more">
            <span className="kw-secondary">已加载 {rows.length} 条</span>
            {list.hasNextPage ? (
              <Button
                color="secondary"
                variant="outline"
                loading={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                加载更多
              </Button>
            ) : null}
          </div>
        </>
      )}
      {opened ? <JobSheet id={opened} kinds={kinds} onClose={() => setOpened(undefined)} /> : null}
    </>
  );
}
