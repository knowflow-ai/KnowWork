/**
 * 运行面板接口（server/src/knowwork_server/jobs/api.py）：后台任务各池的统计、worker 心跳、下游闸门占用，
 * 任务列表与单个任务的阶段；对单个任务取消、立即重跑、删除记录。只给系统管理员，只能在网页上用。
 */
import { request, seg, withQuery } from './http';
import { bool, isRecord, listOf, num, str } from './parse';

export type JobStatus = 'queued' | 'retrying' | 'running' | 'succeeded' | 'failed' | 'canceled';

export interface PoolStats {
  readonly pool: string;
  /** 在线 worker 的执行槽合计 */
  readonly slots: number;
  readonly queued: number;
  readonly retrying: number;
  readonly running: number;
  /** 保留期内（30 天）的失败记录 */
  readonly failed: number;
  /** 到点了还没被领走的任务里等得最久的，秒 */
  readonly oldestWaitSeconds: number | null;
  readonly succeededRecent: number;
  readonly failedRecent: number;
}

export interface WorkerInfo {
  readonly id: string;
  readonly host: string;
  readonly pid: number;
  readonly version: string;
  readonly pools: Readonly<Record<string, number>>;
  readonly busy: Readonly<Record<string, number>>;
  readonly alive: boolean;
  readonly startedAt: string;
  readonly heartbeatAt: string;
}

export interface GateInfo {
  readonly name: string;
  readonly holding: number;
  readonly limit: number;
  /** 经 Redis 共用：上限是全部 worker 合起来的；否则每个 worker 各自这么多 */
  readonly shared: boolean;
}

export interface KindInfo {
  readonly name: string;
  readonly label: string;
  readonly pool: string;
  readonly cancelable: boolean;
}

export interface JobsOverview {
  readonly pools: readonly PoolStats[];
  readonly workers: readonly WorkerInfo[];
  readonly gates: readonly GateInfo[];
  readonly kinds: readonly KindInfo[];
}

export interface JobRow {
  readonly id: string;
  readonly kind: string;
  readonly subjectId: string;
  readonly pool: string;
  readonly status: string;
  readonly stage: string | null;
  readonly tries: number;
  readonly maxTries: number;
  readonly errorCode: string | null;
  readonly errorClass: string | null;
  readonly cancelRequested: boolean;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly nextAttemptAt: string;
  readonly finishedAt: string | null;
}

export interface JobStage {
  readonly tryNo: number;
  readonly name: string;
  readonly status: string;
  readonly errorCode: string | null;
  readonly detail: Readonly<Record<string, unknown>>;
  readonly startedAt: string;
  readonly finishedAt: string | null;
}

export interface JobDetail extends JobRow {
  readonly stages: readonly JobStage[];
}

export interface JobPage {
  readonly items: readonly JobRow[];
  readonly next: string | null;
}

export interface JobQuery {
  readonly status?: JobStatus;
  readonly pool?: string;
  readonly kind?: string;
}

const count = (value: unknown) => num(value) ?? 0;

function counts(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).map(([key, n]) => [key, count(n)]));
}

function poolOf(value: unknown): PoolStats | undefined {
  if (!isRecord(value)) return undefined;
  const pool = str(value.pool);
  if (!pool) return undefined;
  return {
    pool,
    slots: count(value.slots),
    queued: count(value.queued),
    retrying: count(value.retrying),
    running: count(value.running),
    failed: count(value.failed),
    oldestWaitSeconds: num(value.oldest_wait_seconds) ?? null,
    succeededRecent: count(value.succeeded_recent),
    failedRecent: count(value.failed_recent),
  };
}

function workerOf(value: unknown): WorkerInfo | undefined {
  if (!isRecord(value)) return undefined;
  const id = str(value.id);
  if (!id) return undefined;
  return {
    id,
    host: str(value.host) ?? '',
    pid: count(value.pid),
    version: str(value.version) ?? '',
    pools: counts(value.pools),
    busy: counts(value.busy),
    alive: bool(value.alive),
    startedAt: str(value.started_at) ?? '',
    heartbeatAt: str(value.heartbeat_at) ?? '',
  };
}

function gateOf(value: unknown): GateInfo | undefined {
  if (!isRecord(value)) return undefined;
  const name = str(value.name);
  if (!name) return undefined;
  return { name, holding: count(value.holding), limit: count(value.limit), shared: bool(value.shared) };
}

function kindOf(value: unknown): KindInfo | undefined {
  if (!isRecord(value)) return undefined;
  const name = str(value.name);
  if (!name) return undefined;
  return {
    name,
    label: str(value.label) ?? name,
    pool: str(value.pool) ?? '',
    cancelable: bool(value.cancelable, true),
  };
}

function jobOf(value: unknown): JobRow | undefined {
  if (!isRecord(value)) return undefined;
  const id = str(value.id);
  if (!id) return undefined;
  return {
    id,
    kind: str(value.kind) ?? '',
    subjectId: str(value.subject_id) ?? '',
    pool: str(value.pool) ?? '',
    status: str(value.status) ?? '',
    stage: str(value.stage) ?? null,
    tries: count(value.tries),
    maxTries: count(value.max_tries),
    errorCode: str(value.error_code) ?? null,
    errorClass: str(value.error_class) ?? null,
    cancelRequested: bool(value.cancel_requested),
    createdAt: str(value.created_at) ?? '',
    updatedAt: str(value.updated_at) ?? '',
    nextAttemptAt: str(value.next_attempt_at) ?? '',
    finishedAt: str(value.finished_at) ?? null,
  };
}

function stageOf(value: unknown): JobStage | undefined {
  if (!isRecord(value)) return undefined;
  return {
    tryNo: count(value.try_no),
    name: str(value.name) ?? '',
    status: str(value.status) ?? '',
    errorCode: str(value.error_code) ?? null,
    detail: isRecord(value.detail) ? value.detail : {},
    startedAt: str(value.started_at) ?? '',
    finishedAt: str(value.finished_at) ?? null,
  };
}

function requireJob(value: unknown): JobRow {
  const job = jobOf(value);
  if (!job) throw new Error('任务数据无效');
  return job;
}

const BASE = '/api/system/jobs';

export async function getJobsOverview(): Promise<JobsOverview> {
  const body = await request<unknown>(`${BASE}/overview`);
  const record = isRecord(body) ? body : {};
  return {
    pools: listOf(record.pools, poolOf),
    workers: listOf(record.workers, workerOf),
    gates: listOf(record.gates, gateOf),
    kinds: listOf(record.kinds, kindOf),
  };
}

export async function listJobs(query: JobQuery, cursor: string | null): Promise<JobPage> {
  const body = await request<unknown>(withQuery(BASE, { ...query, cursor }));
  const record = isRecord(body) ? body : {};
  return { items: listOf(record.items, jobOf), next: str(record.next) ?? null };
}

export async function getJob(id: string): Promise<JobDetail> {
  const body = await request<unknown>(`${BASE}/${seg(id)}`);
  const record = isRecord(body) ? body : {};
  return { ...requireJob(body), stages: listOf(record.stages, stageOf) };
}

export async function cancelJob(id: string): Promise<JobRow> {
  return requireJob(await request<unknown>(`${BASE}/${seg(id)}/cancel`, { method: 'POST' }));
}

export async function retryJob(id: string): Promise<JobRow> {
  return requireJob(await request<unknown>(`${BASE}/${seg(id)}/retry`, { method: 'POST' }));
}

export async function deleteJob(id: string): Promise<void> {
  await request<unknown>(`${BASE}/${seg(id)}`, { method: 'DELETE' });
}
