/** 通用格式化。 */

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '未知大小';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function truncate(text: string, limit: number): string {
  const single = text.replace(/\s+/g, ' ').trim();
  return single.length > limit ? `${single.slice(0, limit)}…` : single;
}

/** 工具参数 JSON 的一行摘要：优先取路径、地址、查询这类能说明目标的字段。 */
export function describeArguments(raw: string, limit = 80): string {
  if (!raw.trim()) return '';
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      const preferred = ['path', 'file', 'url', 'query', 'command', 'target', 'name'];
      const key = preferred.find((k) => typeof record[k] === 'string') ?? Object.keys(record)[0];
      if (key !== undefined) {
        const value = record[key];
        return truncate(`${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`, limit);
      }
      return '';
    }
    return truncate(String(parsed), limit);
  } catch {
    return truncate(raw, limit);
  }
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** ISO 时间 → 本地「2026-10-19 08:30」；空值或解析不了显示「—」。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}
