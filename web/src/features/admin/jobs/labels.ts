/** 运行面板的中文名、状态色与时长写法。 */
import type { JobRow, JobStatus, KindInfo } from '../../../api/jobs';
import type { Tone } from '../../../ui/StatusDot';

const POOLS: Readonly<Record<string, string>> = {
  core: '解析',
  enrichment: '富化',
  maintenance: '维护',
};

export const poolLabel = (pool: string) => (POOLS[pool] ? `${POOLS[pool]}（${pool}）` : pool);

const GATES: Readonly<Record<string, string>> = { mineru: 'MinerU 文档解析' };

export const gateLabel = (name: string) => GATES[name] ?? name;

export const STATUS_OPTIONS: readonly { readonly value: JobStatus; readonly label: string }[] = [
  { value: 'queued', label: '排队中' },
  { value: 'retrying', label: '等待重试' },
  { value: 'running', label: '运行中' },
  { value: 'succeeded', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'canceled', label: '已取消' },
];

const ERROR_CLASSES: Readonly<Record<string, string>> = {
  file: '文件问题',
  service: '服务问题',
  internal: '内部错误',
};

export const errorClassLabel = (value: string | null) => (value ? (ERROR_CLASSES[value] ?? value) : '');

/** 列表里的状态：排队中又试过的算「等待重试」，打了取消标记还没收尾的算「取消中」。 */
export function jobState(job: JobRow): { readonly label: string; readonly tone: Tone } {
  const open = job.status === 'queued' || job.status === 'running';
  if (open && job.cancelRequested) return { label: '取消中', tone: 'warning' };
  switch (job.status) {
    case 'queued':
      return job.tries > 0 ? { label: '等待重试', tone: 'warning' } : { label: '排队中', tone: 'default' };
    case 'running':
      return { label: '运行中', tone: 'processing' };
    case 'succeeded':
      return { label: '已完成', tone: 'success' };
    case 'failed':
      return { label: '失败', tone: 'error' };
    case 'canceled':
      return { label: '已取消', tone: 'default' };
    default:
      return { label: job.status, tone: 'default' };
  }
}

export function kindLabel(kinds: readonly KindInfo[], name: string): string {
  return kinds.find((kind) => kind.name === name)?.label ?? name;
}

/** 运行中能不能取消：看 worker 报上来的任务类型，删除、导出导入这类做到一半不能停。 */
export function cancelable(kinds: readonly KindInfo[], job: JobRow): boolean {
  if (job.cancelRequested) return false;
  if (job.status === 'queued') return true;
  return job.status === 'running' && (kinds.find((kind) => kind.name === job.kind)?.cancelable ?? true);
}

export const retryable = (job: JobRow) =>
  job.status === 'failed' ||
  job.status === 'canceled' ||
  (job.status === 'queued' && (job.tries > 0 || job.cancelRequested));

export const removable = (job: JobRow) => job.status === 'failed' || job.status === 'canceled';

/** 等了多久：秒、分钟、小时各一档。 */
export function waited(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
}

/** 各池执行槽写成一行：「解析 2 · 富化 2」。 */
export function slotsLine(pools: Readonly<Record<string, number>>): string {
  const entries = Object.entries(pools);
  if (entries.length === 0) return '—';
  return entries.map(([pool, size]) => `${POOLS[pool] ?? pool} ${size}`).join(' · ');
}
