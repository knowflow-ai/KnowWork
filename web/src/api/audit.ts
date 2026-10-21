/** 审计日志查询（server/src/knowwork_server/audit/api.py），只有空间 owner/admin 能看。 */
import { request, seg } from './http';
import { isRecord, listOf, num, str, text } from './parse';
import { WORKSPACE_ID } from './workspace';

export type ActorType = 'human' | 'agent' | 'system';
export type AuditResult = 'success' | 'denied' | 'failed';

export interface AuditEntry {
  readonly id: string;
  readonly occurredAt: string;
  readonly actorType: string;
  readonly actorId: string | null;
  readonly action: string;
  readonly resourceType: string | null;
  readonly resourceId: string | null;
  readonly result: string;
  readonly statusCode: number | null;
  readonly ip: string | null;
  readonly detail: Readonly<Record<string, unknown>>;
}

export interface AuditFilter {
  readonly actorType?: ActorType;
  readonly result?: AuditResult;
  /** 动作名，末尾写 .* 表示前缀。 */
  readonly action?: string;
}

const ACTION_PATTERN = /^[a-z0-9_]+(\.[a-z0-9_]+)*(\.\*)?$/;

export function validateAction(value: string): string | null {
  const action = value.trim();
  return !action || ACTION_PATTERN.test(action)
    ? null
    : '动作名只能是小写字母、数字、下划线与点，末尾可写 .*';
}

function parseEntry(value: unknown): AuditEntry | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const action = text(value.action);
  if (!id || !action) return undefined;
  return {
    id,
    occurredAt: str(value.occurredAt) ?? '',
    actorType: str(value.actorType) ?? 'system',
    actorId: text(value.actorId) ?? null,
    action,
    resourceType: text(value.resourceType) ?? null,
    resourceId: text(value.resourceId) ?? null,
    result: str(value.result) ?? '',
    statusCode: num(value.statusCode) ?? null,
    ip: text(value.ip) ?? null,
    detail: isRecord(value.detail) ? value.detail : {},
  };
}

export async function listAuditLogs(
  filter: AuditFilter,
  cursor?: string | null,
): Promise<{ readonly items: readonly AuditEntry[]; readonly next: string | null }> {
  const body = await request<unknown>(`/api/workspaces/${seg(WORKSPACE_ID)}/audit-logs`, {
    query: {
      actorType: filter.actorType,
      result: filter.result,
      action: filter.action?.trim() || undefined,
      limit: 50,
      cursor: cursor ?? undefined,
    },
  });
  const record = isRecord(body) ? body : {};
  return { items: listOf(record.items, parseEntry), next: text(record.next) ?? null };
}
