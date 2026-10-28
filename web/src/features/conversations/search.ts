/**
 * 搜索对话的纯函数：没输入时把最近的对话按时间分组（今天、昨天、前 7 天、前 30 天、更早，照 ChatGPT），
 * 命中的片段按查询词切开好把命中处标出来（不分大小写）。
 */
import type { ConversationSummary } from '../../api/conversations';

export interface Group<T> {
  readonly label: string;
  readonly items: readonly T[];
}

const DAY = 24 * 60 * 60 * 1000;

function startOfDay(at: number): number {
  const date = new Date(at);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

export function groupByTime<T extends ConversationSummary>(items: readonly T[], now: number): Group<T>[] {
  const today = startOfDay(now);
  const bounds: readonly [string, number][] = [
    ['今天', today],
    ['昨天', today - DAY],
    ['前 7 天', today - 7 * DAY],
    ['前 30 天', today - 30 * DAY],
  ];
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const at = item.updatedAt ? new Date(item.updatedAt).getTime() : 0;
    const label = bounds.find(([, from]) => at >= from)?.[0] ?? '更早';
    groups.set(label, [...(groups.get(label) ?? []), item]);
  }
  return [...bounds.map(([label]) => label), '更早']
    .filter((label) => groups.has(label))
    .map((label) => ({ label, items: groups.get(label) ?? [] }));
}

/** 「出差去上海的差旅报销」按「差旅」切成 [出差去上海的, 差旅(命中), 报销]。 */
export function splitHits(text: string, query: string): { readonly text: string; readonly hit: boolean }[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [{ text, hit: false }];
  const parts: { text: string; hit: boolean }[] = [];
  const lower = text.toLowerCase();
  let at = 0;
  for (let found = lower.indexOf(needle); found >= 0; found = lower.indexOf(needle, at)) {
    if (found > at) parts.push({ text: text.slice(at, found), hit: false });
    parts.push({ text: text.slice(found, found + needle.length), hit: true });
    at = found + needle.length;
  }
  if (at < text.length) parts.push({ text: text.slice(at), hit: false });
  return parts;
}
