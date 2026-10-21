/** 把接口返回的不可信 JSON 收窄成可用字段的小工具。字段不对时返回 undefined 或默认值，不抛异常。 */

export type Json = Record<string, unknown>;

export function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

export function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined;
}

export function num(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  // 后端的 Decimal 可能序列化成字符串
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return undefined;
}

export function bool(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function strings(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/** 列表接口：不是数组时当空列表；逐项解析，坏的一项丢掉。 */
export function listOf<T>(value: unknown, parse: (item: unknown) => T | undefined): readonly T[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const parsed = parse(item);
    return parsed === undefined ? [] : [parsed];
  });
}
