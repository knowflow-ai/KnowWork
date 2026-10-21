/**
 * 把事件流里不可信的数据收窄成可用的字段。任何字段不对就返回 undefined，
 * 由调用方丢弃这一帧，绝不因为一帧坏数据让整个页面出错。
 */
import { isRecord, num, str } from '../../../api/parse';
import type { Cursor, V1Event } from './types';

export { isRecord, num, str };

export const SCHEMA_VERSION = 'events.v1';
const ID_PATTERN = /^(\d{1,15})(?:\.(\d{1,6}))?$/;

/** SSE id → 位置；格式不对返回 undefined。 */
export function parseCursor(id: string | undefined): Cursor | undefined {
  const match = id ? ID_PATTERN.exec(id) : null;
  if (!match) return undefined;
  const seq = Number(match[1]);
  if (!Number.isSafeInteger(seq) || seq <= 0) return undefined;
  return { seq, index: match[2] === undefined ? Number.POSITIVE_INFINITY : Number(match[2]) };
}

export function compareCursor(a: Cursor, b: Cursor): number {
  if (a.seq !== b.seq) return a.seq - b.seq;
  if (a.index === b.index) return 0;
  return a.index < b.index ? -1 : 1;
}

const nullableStr = (value: unknown): string | null => (typeof value === 'string' && value ? value : null);

export function parseV1Event(value: unknown): V1Event | undefined {
  if (!isRecord(value) || value.schema_version !== SCHEMA_VERSION) return undefined;
  const type = str(value.type);
  const seq = num(value.seq);
  if (!type || seq === undefined) return undefined;
  return {
    type,
    seq,
    runId: nullableStr(value.run_id),
    time: num(value.time) ?? 0,
    itemId: nullableStr(value.item_id),
    kind: nullableStr(value.kind),
    data: isRecord(value.data) ? value.data : {},
  };
}

const PREVIEW_LIMIT = 400;

export function preview(value: string): string {
  return value.length > PREVIEW_LIMIT ? `${value.slice(0, PREVIEW_LIMIT)}…` : value;
}

/** 工具参数可能是字符串或对象，统一成字符串交给摘要函数。 */
export function argumentsText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  try {
    return JSON.stringify(value);
  } catch {
    return '';
  }
}
