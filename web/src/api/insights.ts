/**
 * 问答大盘（/api/workspaces/{ws}/insights?days=7|30|90）：每轮汇总的聚合，只给空间所有者和管理员，
 * 别人看是 403。数字都能点到运行记录里具体是哪几轮（runsLink 拼好筛选条件）。
 */
import { request } from './http';
import { isRecord, listOf, num, str, type Json } from './parse';
import { workspacePath } from './workspace';

export const INSIGHT_RANGES = [7, 30, 90] as const;
export type InsightRange = (typeof INSIGHT_RANGES)[number];

export interface InsightTotals {
  readonly turns: number;
  readonly askers: number;
  readonly completed: number;
  readonly failed: number;
  readonly cancelled: number;
  readonly liked: number;
  readonly disliked: number;
  /** 答完了、检索过知识库的轮数（「检索了没引用」占比的分母） */
  readonly searched: number;
  /** 估算费用，没填单价的模型记 0 */
  readonly cost: string;
  readonly inputTokens: number;
  readonly outputTokens: number;
}

export interface InsightDay {
  readonly date: string;
  readonly turns: number;
  readonly completed: number;
  /** 已结束的（完成 + 失败 + 取消），完成率的分母 */
  readonly ended: number;
}

export interface InsightCount {
  /** 结束原因（error、blocked、aborted……）或点踩分类，空串是没选分类 */
  readonly key: string;
  readonly count: number;
  /** 结束原因所属的状态：failed | cancelled */
  readonly status: string;
}

export interface ToolUse {
  readonly name: string;
  readonly turns: number;
  readonly failed: number;
}

export interface BaseUse {
  /** 知识库全局 id，下钻运行记录按它筛 */
  readonly id: string;
  readonly name: string;
  readonly turns: number;
  readonly cited: number;
  readonly unused: number;
  readonly disliked: number;
}

/** 统计用的时间段：起止日期（显示用）与起止时刻（下钻运行记录按它筛，两边数得上） */
export interface InsightWindow {
  readonly start: string;
  readonly end: string;
  readonly since: string;
  readonly until: string;
}

export interface Insights extends InsightWindow {
  readonly totals: InsightTotals;
  readonly previous: InsightTotals;
  readonly days: readonly InsightDay[];
  readonly endReasons: readonly InsightCount[];
  readonly dislikes: readonly InsightCount[];
  readonly uncited: number;
  readonly tools: readonly ToolUse[];
  readonly bases: readonly BaseUse[];
}

const record = (value: unknown): Json => (isRecord(value) ? value : {});
const count = (value: unknown): number => num(value) ?? 0;

function totalsOf(value: unknown): InsightTotals {
  const r = record(value);
  return {
    turns: count(r.turns),
    askers: count(r.askers),
    completed: count(r.completed),
    failed: count(r.failed),
    cancelled: count(r.cancelled),
    liked: count(r.liked),
    disliked: count(r.disliked),
    searched: count(r.searched),
    cost: str(r.cost) ?? '0',
    inputTokens: count(r.inputTokens),
    outputTokens: count(r.outputTokens),
  };
}

function dayOf(value: unknown): InsightDay | undefined {
  const r = record(value);
  const date = str(r.date);
  return date
    ? { date, turns: count(r.turns), completed: count(r.completed), ended: count(r.ended) }
    : undefined;
}

function countOf(value: unknown): InsightCount | undefined {
  const r = record(value);
  const key = str(r.key);
  return key === undefined ? undefined : { key, count: count(r.count), status: str(r.status) ?? '' };
}

function toolOf(value: unknown): ToolUse | undefined {
  const r = record(value);
  const name = str(r.name);
  return name ? { name, turns: count(r.turns), failed: count(r.failed) } : undefined;
}

function baseOf(value: unknown): BaseUse | undefined {
  const r = record(value);
  const id = str(r.id);
  if (!id) return undefined;
  return {
    id,
    name: str(r.name) ?? id,
    turns: count(r.turns),
    cited: count(r.cited),
    unused: count(r.unused),
    disliked: count(r.disliked),
  };
}

export function insightsOf(value: unknown): Insights {
  const r = record(value);
  return {
    start: str(r.start) ?? '',
    end: str(r.end) ?? '',
    since: str(r.since) ?? '',
    until: str(r.until) ?? '',
    totals: totalsOf(r.totals),
    previous: totalsOf(r.previous),
    days: listOf(r.days, dayOf),
    endReasons: listOf(r.endReasons, countOf),
    dislikes: listOf(r.dislikes, countOf),
    uncited: count(r.uncited),
    tools: listOf(r.tools, toolOf),
    bases: listOf(r.bases, baseOf),
  };
}

export async function getInsights(days: InsightRange): Promise<Insights> {
  return insightsOf(await request<unknown>(workspacePath(`/insights?days=${days}`)));
}

/** 下钻到运行记录：时间段同大盘（精确到时刻），再带上筛选条件（状态、信号、工具、知识库）。 */
export function runsLink(window: InsightWindow, filter: Readonly<Record<string, string>>): string {
  const range = { since: window.since, until: window.until, from: window.start, to: window.end };
  return `/admin/runs?${new URLSearchParams({ ...range, ...filter }).toString()}`;
}
