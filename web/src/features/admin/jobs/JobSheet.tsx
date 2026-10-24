/** 单个任务的详情抽屉：基本信息与每次尝试的各阶段（只有阶段名、状态、错误码与计数，没有文件内容）。 */
import type { JobStage, KindInfo } from '../../../api/jobs';
import { formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Empty } from '../../../ui/Empty';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot, type Tone } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { duration } from '../../debug/format';
import { errorClassLabel, jobState, kindLabel, poolLabel } from './labels';
import { useJob } from './queries';

const STAGE_STATES: Readonly<Record<string, { readonly label: string; readonly tone: Tone }>> = {
  running: { label: '进行中', tone: 'processing' },
  done: { label: '完成', tone: 'success' },
  failed: { label: '失败', tone: 'error' },
  canceled: { label: '中止', tone: 'default' },
};

function spent(stage: JobStage): number | null {
  if (!stage.finishedAt) return null;
  return new Date(stage.finishedAt).getTime() - new Date(stage.startedAt).getTime();
}

const detailLine = (detail: Readonly<Record<string, unknown>>) =>
  Object.entries(detail)
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join('，') || '—';

const STAGE_COLUMNS: readonly Column<JobStage>[] = [
  { key: 'try', title: '第几次', width: 70, align: 'right', render: (row) => row.tryNo },
  { key: 'name', title: '阶段', width: 120, ellipsis: true, render: (row) => row.name },
  {
    key: 'status',
    title: '状态',
    width: 90,
    render: (row) => {
      const state = STAGE_STATES[row.status] ?? { label: row.status, tone: 'default' as Tone };
      return <StatusDot tone={state.tone}>{state.label}</StatusDot>;
    },
  },
  { key: 'error', title: '错误码', width: 140, ellipsis: true, render: (row) => row.errorCode ?? '—' },
  { key: 'spent', title: '耗时', width: 100, render: (row) => duration(spent(row)) },
  { key: 'detail', title: '记录', ellipsis: true, render: (row) => detailLine(row.detail) },
];

interface Props {
  readonly id: string;
  readonly kinds: readonly KindInfo[];
  readonly onClose: () => void;
}

export function JobSheet({ id, kinds, onClose }: Props) {
  const job = useJob(id);
  return (
    <Sheet title="任务详情" onClose={onClose} width={800}>
      {job.isPending ? (
        <Skeleton rows={6} />
      ) : job.isError ? (
        <Empty title="任务详情加载失败" description="任务可能已经被清理，请刷新列表后再看" />
      ) : (
        <>
          <DescriptionList
            items={[
              { label: '任务 ID', value: <span className="kw-num">{job.data.id}</span> },
              { label: '类型', value: `${kindLabel(kinds, job.data.kind)}（${job.data.kind}）` },
              { label: '对象 ID', value: <span className="kw-num">{job.data.subjectId}</span> },
              { label: '任务池', value: poolLabel(job.data.pool) },
              { label: '状态', value: jobState(job.data).label },
              { label: '尝试次数', value: `${job.data.tries} / ${job.data.maxTries}` },
              {
                label: '错误',
                value: job.data.errorCode
                  ? `${job.data.errorCode}${job.data.errorClass ? `（${errorClassLabel(job.data.errorClass)}）` : ''}`
                  : '—',
              },
              { label: '创建时间', value: formatDateTime(job.data.createdAt) },
              {
                label: job.data.status === 'queued' ? '下次执行' : '结束时间',
                value: formatDateTime(
                  job.data.status === 'queued' ? job.data.nextAttemptAt : job.data.finishedAt,
                ),
              },
            ]}
          />
          <h3 className="kw-jobs-subhead">各阶段</h3>
          <Table<JobStage>
            aria-label="各阶段"
            rowKey={(row) => `${row.tryNo}:${row.name}:${row.startedAt}`}
            columns={STAGE_COLUMNS}
            rows={job.data.stages}
            minWidth={700}
            pageSize={false}
            empty="还没有开始执行"
          />
          <p className="kw-secondary">
            错误详情可能带出文件内容，这里不显示；排查时按任务 ID 在 worker 日志里找。
          </p>
        </>
      )}
    </Sheet>
  );
}
