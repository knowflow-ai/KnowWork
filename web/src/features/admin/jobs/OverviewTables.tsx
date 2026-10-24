/** 运行面板上半部分：各池统计、下游并发、worker 心跳。 */
import type { GateInfo, PoolStats, WorkerInfo } from '../../../api/jobs';
import { formatDateTime } from '../../../utils/format';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { gateLabel, poolLabel, slotsLine, waited } from './labels';

const POOL_COLUMNS: readonly Column<PoolStats>[] = [
  { key: 'pool', title: '任务池', width: 150, render: (row) => poolLabel(row.pool) },
  { key: 'slots', title: '执行槽', width: 80, align: 'right', render: (row) => row.slots },
  { key: 'queued', title: '排队', width: 80, align: 'right', render: (row) => row.queued },
  { key: 'retrying', title: '等待重试', width: 90, align: 'right', render: (row) => row.retrying },
  { key: 'running', title: '运行中', width: 80, align: 'right', render: (row) => row.running },
  { key: 'failed', title: '失败记录', width: 90, align: 'right', render: (row) => row.failed },
  { key: 'wait', title: '最久等待', width: 110, render: (row) => waited(row.oldestWaitSeconds) },
  {
    key: 'recent',
    title: '近 24 小时完成 / 失败',
    width: 170,
    render: (row) => (
      <span className="kw-num">
        {row.succeededRecent} / {row.failedRecent}
      </span>
    ),
  },
];

export function PoolTable({ pools }: { readonly pools: readonly PoolStats[] }) {
  return (
    <Table<PoolStats>
      aria-label="任务池"
      rowKey={(row) => row.pool}
      columns={POOL_COLUMNS}
      rows={pools}
      minWidth={850}
      pageSize={false}
      empty="还没有后台任务"
    />
  );
}

const GATE_COLUMNS: readonly Column<GateInfo>[] = [
  { key: 'name', title: '下游服务', width: 180, render: (row) => gateLabel(row.name) },
  {
    key: 'use',
    title: '占用 / 上限',
    width: 120,
    render: (row) => (
      <span className="kw-num">
        {row.holding} / {row.limit}
      </span>
    ),
  },
  {
    key: 'shared',
    title: '说明',
    render: (row) =>
      row.shared ? '所有 worker 共用这些名额' : '每个 worker 各自计算（没有配置 Redis，或 Redis 暂时连不上）',
  },
];

export function GateTable({ gates }: { readonly gates: readonly GateInfo[] }) {
  return (
    <Table<GateInfo>
      aria-label="下游并发"
      rowKey={(row) => row.name}
      columns={GATE_COLUMNS}
      rows={gates}
      pageSize={false}
      empty="worker 启动以来还没有调用过受并发限制的下游服务"
    />
  );
}

const busyTotal = (worker: WorkerInfo) => Object.values(worker.busy).reduce((sum, n) => sum + n, 0);

const WORKER_COLUMNS: readonly Column<WorkerInfo>[] = [
  {
    key: 'host',
    title: 'Worker',
    ellipsis: true,
    render: (row) => (
      <span className="kw-cell-stack">
        <span>{row.host}</span>
        <span className="kw-secondary">
          进程 {row.pid} · 版本 {row.version}
        </span>
      </span>
    ),
  },
  { key: 'slots', title: '执行槽', width: 200, render: (row) => slotsLine(row.pools) },
  { key: 'busy', title: '在跑', width: 70, align: 'right', render: busyTotal },
  {
    key: 'state',
    title: '心跳',
    width: 190,
    render: (row) => (
      <span className="kw-cell-stack">
        <StatusDot tone={row.alive ? 'success' : 'error'}>{row.alive ? '在线' : '失联'}</StatusDot>
        <span className="kw-secondary">{formatDateTime(row.heartbeatAt)}</span>
      </span>
    ),
  },
  {
    key: 'started',
    title: '启动时间',
    width: 150,
    render: (row) => <span className="kw-num">{formatDateTime(row.startedAt)}</span>,
  },
];

export function WorkerTable({ workers }: { readonly workers: readonly WorkerInfo[] }) {
  return (
    <Table<WorkerInfo>
      aria-label="Worker"
      rowKey={(row) => row.id}
      columns={WORKER_COLUMNS}
      rows={workers}
      minWidth={760}
      pageSize={false}
      empty="没有在线的 worker，后台任务不会执行。请检查 worker 进程有没有启动。"
    />
  );
}
