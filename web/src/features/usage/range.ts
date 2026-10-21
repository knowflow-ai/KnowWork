import type { UsageRow } from '../../api/models';

/** 用量的时间范围：只给几个常用档位，起止日期按本机日历算，形如 2026-10-19（含首尾）。 */

export type RangeKey = '7d' | '30d' | 'month';

export const RANGES: readonly { readonly value: RangeKey; readonly label: string }[] = [
  { value: '7d', label: '最近 7 天' },
  { value: '30d', label: '最近 30 天' },
  { value: 'month', label: '本月' },
];

const pad = (n: number) => String(n).padStart(2, '0');

export function isoDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function rangeOf(
  key: RangeKey,
  today: Date = new Date(),
): { readonly start: string; readonly end: string } {
  const end = isoDate(today);
  if (key === 'month') return { start: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), end };
  const days = key === '7d' ? 6 : 29;
  return { start: isoDate(new Date(today.getFullYear(), today.getMonth(), today.getDate() - days)), end };
}

/** 各行相加得到合计行。 */
export function totals(rows: readonly UsageRow[]): UsageRow {
  return rows.reduce(
    (acc, r) => ({
      key: '合计',
      calls: acc.calls + r.calls,
      inputTokens: acc.inputTokens + r.inputTokens,
      outputTokens: acc.outputTokens + r.outputTokens,
      totalTokens: acc.totalTokens + r.totalTokens,
      cost: acc.cost + r.cost,
    }),
    { key: '合计', calls: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, cost: 0 },
  );
}
