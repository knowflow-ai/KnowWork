/**
 * 技能运行事实（server/src/knowwork_server/skills/runs_api.py）：某个空间技能按版本的汇总与最近的运行，
 * 只有空间管理员能看。对话里的运行带会话与轮次，可以打开那一轮的运行详情。
 */
import { request, seg } from './http';
import { isRecord, listOf, num, str, text } from './parse';
import { WORKSPACE_ID } from './workspace';

export type SkillRunStatus = 'succeeded' | 'failed' | 'cancelled';

export interface SkillRunRow {
  readonly id: string;
  readonly version: string;
  readonly selectionMode: string;
  readonly status: SkillRunStatus;
  readonly failureStage: string | null;
  readonly failureCode: string | null;
  readonly userId: string;
  readonly startedAt: string;
  readonly durationMs: number | null;
  readonly totalTokens: number;
  readonly cost: string | null;
  readonly artifactCount: number;
  readonly steps: number;
  readonly failedSteps: number;
  readonly conversationId: string | null;
  readonly turn: number | null;
  readonly title: string;
}

export interface SkillVersionStats {
  readonly version: string;
  readonly runs: number;
  readonly succeeded: number;
  readonly failed: number;
  readonly cancelled: number;
  readonly avgDurationMs: number | null;
  readonly avgTokens: number;
  readonly topFailureStage: string | null;
  readonly lastRunAt: string;
}

export interface SkillRunFilter {
  readonly since: string;
  readonly status?: SkillRunStatus;
  readonly version?: string;
}

const STATUSES: readonly SkillRunStatus[] = ['succeeded', 'failed', 'cancelled'];

const base = (skillId: string, path = '') =>
  `/api/workspaces/${seg(WORKSPACE_ID)}/skills/${seg(skillId)}/runs${path}`;

const orNull = (value: unknown) => text(value) ?? null;

function parseRun(value: unknown): SkillRunRow | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const startedAt = text(value.startedAt);
  const status = STATUSES.find((s) => s === value.status);
  if (!id || !startedAt || !status) return undefined;
  return {
    id,
    version: str(value.version) ?? '',
    selectionMode: str(value.selectionMode) ?? '',
    status,
    failureStage: orNull(value.failureStage),
    failureCode: orNull(value.failureCode),
    userId: str(value.userId) ?? '',
    startedAt,
    durationMs: num(value.durationMs) ?? null,
    totalTokens: num(value.totalTokens) ?? 0,
    cost: orNull(value.cost),
    artifactCount: num(value.artifactCount) ?? 0,
    steps: num(value.steps) ?? 0,
    failedSteps: num(value.failedSteps) ?? 0,
    conversationId: orNull(value.conversationId),
    turn: num(value.turn) ?? null,
    title: str(value.title) ?? '',
  };
}

function parseStats(value: unknown): SkillVersionStats | undefined {
  if (!isRecord(value)) return undefined;
  const version = text(value.version);
  const lastRunAt = text(value.lastRunAt);
  if (!version || !lastRunAt) return undefined;
  return {
    version,
    runs: num(value.runs) ?? 0,
    succeeded: num(value.succeeded) ?? 0,
    failed: num(value.failed) ?? 0,
    cancelled: num(value.cancelled) ?? 0,
    avgDurationMs: num(value.avgDurationMs) ?? null,
    avgTokens: num(value.avgTokens) ?? 0,
    topFailureStage: orNull(value.topFailureStage),
    lastRunAt,
  };
}

function query(filter: SkillRunFilter, before?: string | null): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filter)) if (value) params.set(key, value);
  if (before) params.set('before', before);
  const text = params.toString();
  return text ? `?${text}` : '';
}

export async function listSkillRuns(
  skillId: string,
  filter: SkillRunFilter,
  before: string | null,
): Promise<{ readonly items: readonly SkillRunRow[]; readonly next: string | null }> {
  const body = await request<unknown>(base(skillId, query(filter, before)));
  const value = isRecord(body) ? body : {};
  return { items: listOf(value.items, parseRun), next: orNull(value.next) };
}

export async function skillRunSummary(skillId: string, since: string): Promise<readonly SkillVersionStats[]> {
  const body = await request<unknown>(base(skillId, `/summary${query({ since })}`));
  return listOf(isRecord(body) ? body.versions : undefined, parseStats);
}
