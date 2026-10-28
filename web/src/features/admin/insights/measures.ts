/** 问答大盘上的数怎么算、怎么写：完成率、和上一段比、结束原因与点踩分类的中文名。 */
import { FEEDBACK_CATEGORIES } from '../../../api/feedback';
import type { InsightTotals } from '../../../api/insights';

/** 完成的占已结束（完成 + 失败 + 取消）的比例；还没有结束的为 null（不是 0%）。 */
export function completionRate(completed: number, ended: number): number | null {
  return ended > 0 ? completed / ended : null;
}

export const endedOf = (t: InsightTotals): number => t.completed + t.failed + t.cancelled;

export const percent = (rate: number | null): string => (rate === null ? '—' : `${Math.round(rate * 100)}%`);

export const number = (n: number): string => n.toLocaleString('zh-CN');

/** 占比：保留一位小数（6.9%），分母为 0 时为 null。比例小的时候整数会把 0.4% 写成 0%。 */
export function share(part: number, whole: number): string | null {
  return whole > 0 ? `${Number(((part / whole) * 100).toFixed(1))}%` : null;
}

/** 和上一段同样长的时间比：多了、少了几个，或持平；上一段没有数据时不比。 */
export function changeOf(now: number, before: number, unit = ''): string {
  if (before === 0) return now === 0 ? '上一段也没有' : '上一段没有';
  const diff = now - before;
  if (diff === 0) return '和上一段持平';
  return `比上一段${diff > 0 ? '多' : '少'} ${number(Math.abs(diff))}${unit}`;
}

/** 比例的变化按百分点写。 */
export function rateChangeOf(now: number | null, before: number | null): string {
  if (now === null || before === null) return '上一段没有可比的数据';
  const points = Math.round((now - before) * 100);
  if (points === 0) return '和上一段持平';
  return `比上一段${points > 0 ? '高' : '低'} ${Math.abs(points)} 个百分点`;
}

/** 宿主给的结束原因：出错、被拦截；用户取消叫 aborted */
const REASONS: Readonly<Record<string, string>> = {
  error: '出错',
  blocked: '被拦截',
  aborted: '用户取消',
  cancelled: '用户取消',
  interrupted: '中断',
};

export const reasonLabel = (reason: string): string => REASONS[reason] ?? (reason || '原因不明');

const CATEGORIES = new Map<string, string>(FEEDBACK_CATEGORIES.map((c) => [c.value, c.label]));

export const categoryLabel = (category: string): string =>
  category ? (CATEGORIES.get(category) ?? category) : '没选分类';
