/** MCP 服务（server/src/knowwork_server/tools/mcp/api.py）。字段是 camelCase；响应里没有请求头明文。 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, text } from './parse';
import { WORKSPACE_ID } from './workspace';

const base = (path = '') => `/api/workspaces/${seg(WORKSPACE_ID)}/mcp-servers${path}`;

export type ToolRisk = 'read' | 'write' | 'dangerous';
export type ToolApproval = 'ask' | 'never';

export const RISKS: readonly { readonly value: ToolRisk; readonly label: string }[] = [
  { value: 'read', label: '只读' },
  { value: 'write', label: '写入' },
  { value: 'dangerous', label: '高风险' },
];
export const APPROVALS: readonly { readonly value: ToolApproval; readonly label: string }[] = [
  { value: 'ask', label: '执行前审批' },
  { value: 'never', label: '直接执行' },
];

/** 服务名：小写字母开头，只含小写字母、数字、连字符（与 naming.SERVER_NAME_PATTERN 一致）。 */
export const SERVER_NAME = /^[a-z][a-z0-9-]{0,23}$/;
const URL = /^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/;
const HEADER_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/;
const RESERVED_HEADERS = new Set([
  'host',
  'content-length',
  'content-type',
  'accept',
  'connection',
  'transfer-encoding',
  'mcp-session-id',
  'mcp-protocol-version',
  'last-event-id',
]);

export interface McpTool {
  readonly name: string;
  readonly rawName: string;
  readonly description: string;
  readonly risk: ToolRisk;
  readonly riskOverridden: boolean;
  readonly approval: ToolApproval;
  readonly approvalOverridden: boolean;
  readonly enabled: boolean;
  readonly problem: string | null;
}

export interface McpServer {
  readonly id: string;
  readonly name: string;
  readonly title: string;
  readonly url: string;
  readonly headers: readonly { readonly name: string; readonly last4: string }[];
  readonly timeoutSeconds: number;
  readonly enabled: boolean;
  readonly tools: readonly McpTool[];
  /** 按服务授权用的资源 id，例如 mcp__github__*。 */
  readonly grantPattern: string;
  readonly syncedAt: string | null;
  readonly syncError: string | null;
}

const risk = (value: unknown): ToolRisk => (value === 'read' || value === 'dangerous' ? value : 'write');

function parseTool(value: unknown): McpTool | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  if (!name) return undefined;
  return {
    name,
    rawName: str(value.rawName) ?? name,
    description: str(value.description) ?? '',
    risk: risk(value.risk),
    riskOverridden: bool(value.riskOverridden),
    approval: value.approval === 'never' ? 'never' : 'ask',
    approvalOverridden: bool(value.approvalOverridden),
    enabled: bool(value.enabled, true),
    problem: text(value.problem) ?? null,
  };
}

function parseHeader(value: unknown) {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  return name ? { name, last4: str(value.last4) ?? '' } : undefined;
}

export function parseServer(value: unknown): McpServer | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    title: str(value.title) ?? name,
    url: str(value.url) ?? '',
    headers: listOf(value.headers, parseHeader),
    timeoutSeconds: num(value.timeoutSeconds) ?? 60,
    enabled: bool(value.enabled, true),
    tools: listOf(value.tools, parseTool),
    grantPattern: str(value.grantPattern) ?? `mcp__${name}__*`,
    syncedAt: text(value.syncedAt) ?? null,
    syncError: text(value.syncError) ?? null,
  };
}

export async function listServers(): Promise<readonly McpServer[]> {
  return listOf(await request<unknown>(base()), parseServer);
}

export interface HeaderInput {
  readonly name: string;
  readonly value: string;
}

export interface ServerInput {
  readonly name: string;
  readonly title: string;
  readonly url: string;
  readonly timeoutSeconds: number;
  /** undefined 表示不改（修改时）；新建时不填即没有请求头。 */
  readonly headers?: readonly HeaderInput[];
}

export function validateServerInput(input: ServerInput, creating: boolean): string | null {
  if (creating && !SERVER_NAME.test(input.name)) {
    return '标识须以小写字母开头，只含小写字母、数字和连字符，最多 24 个字符';
  }
  const title = input.title.trim();
  if (!title || title.length > 100) return '名称须为 1 到 100 个字';
  if (!URL.test(input.url.trim()))
    return '地址必须是 http(s):// 开头的完整地址，且不带查询参数；凭据请放在请求头里';
  if (!Number.isInteger(input.timeoutSeconds) || input.timeoutSeconds < 5 || input.timeoutSeconds > 300) {
    return '超时须为 5 到 300 秒';
  }
  const names = (input.headers ?? []).map((h) => h.name.trim());
  if (names.length > 10) return '请求头最多 10 个';
  for (const header of input.headers ?? []) {
    const name = header.name.trim();
    if (!HEADER_NAME.test(name) || RESERVED_HEADERS.has(name.toLowerCase()))
      return `请求头 ${name} 的名字不合法`;
    if (!header.value.trim()) return `请求头 ${name} 的值不能为空`;
  }
  if (new Set(names.map((n) => n.toLowerCase())).size !== names.length) return '请求头有重复的名字';
  return null;
}

function headersBody(headers: readonly HeaderInput[]): Record<string, string> {
  return Object.fromEntries(headers.map((h) => [h.name.trim(), h.value.trim()]));
}

export async function createServer(input: ServerInput): Promise<McpServer | undefined> {
  const body = {
    name: input.name,
    title: input.title.trim(),
    url: input.url.trim(),
    timeoutSeconds: input.timeoutSeconds,
    headers: headersBody(input.headers ?? []),
  };
  return parseServer(await request<unknown>(base(), { method: 'POST', body }));
}

export async function updateServer(
  id: string,
  input: Omit<ServerInput, 'name'>,
): Promise<McpServer | undefined> {
  const body: Record<string, unknown> = {
    title: input.title.trim(),
    url: input.url.trim(),
    timeoutSeconds: input.timeoutSeconds,
  };
  if (input.headers !== undefined) body.headers = headersBody(input.headers);
  return parseServer(await request<unknown>(base(`/${seg(id)}`), { method: 'PATCH', body }));
}

export function setServerEnabled(id: string, enabled: boolean): Promise<unknown> {
  return request(base(`/${seg(id)}`), { method: 'PATCH', body: { enabled } });
}

export function syncServer(id: string): Promise<unknown> {
  return request(base(`/${seg(id)}/sync`), { method: 'POST' });
}

export function deleteServer(id: string): Promise<void> {
  return request(base(`/${seg(id)}`), { method: 'DELETE' });
}

export interface ToolSettings {
  readonly risk?: ToolRisk | 'auto';
  readonly approval?: ToolApproval | 'auto';
  readonly enabled?: boolean;
}

export function configureTool(serverId: string, tool: string, settings: ToolSettings): Promise<unknown> {
  return request(base(`/${seg(serverId)}/tools/${seg(tool)}`), { method: 'PATCH', body: settings });
}
