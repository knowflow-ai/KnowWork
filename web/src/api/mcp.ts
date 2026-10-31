/**
 * MCP 服务（server/src/knowwork_server/tools/mcp/api.py）。字段是 camelCase；响应里没有请求头明文。
 * 管理接口按范围建（mcpAdminApi）：空间服务在 /api/workspaces/{ws}/mcp-servers，扩展的企业 MCP 在 /api/system/mcp-servers，
 * 请求与返回一样；本文件导出的 listServers、createServer…… 是空间服务那一份。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, text } from './parse';
import { workspacePath } from './workspace';

/** 每个工具一个三档：允许（直接调）、需确认（调用前请用户批准）、禁用（不给模型）。 */
export type ToolPolicy = 'allow' | 'ask' | 'block';
/** 分组：只读、写入（「高风险」并进写入），只影响分组与运行记录里的风险标注。 */
export type ToolGroup = 'read' | 'write';

export const POLICIES: readonly { readonly value: ToolPolicy; readonly label: string }[] = [
  { value: 'allow', label: '允许' },
  { value: 'ask', label: '需确认' },
  { value: 'block', label: '禁用' },
];

/** 服务名：小写字母开头，只含小写字母、数字、连字符（与 naming.SERVER_NAME_PATTERN 一致）。 */
export const SERVER_NAME = /^[a-z][a-z0-9-]{0,23}$/;
const URL = /^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/;
// 允许下划线：HTTP 本身允许，也有服务这么起名（例如 CONTEXT7_API_KEY）
const HEADER_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
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
  readonly policy: ToolPolicy;
  readonly group: ToolGroup;
  /** 对方注解里的分组：和 group 不同说明管理员挪过组 */
  readonly annotatedGroup: ToolGroup;
  /** 之后同步才出现、管理员还没看过（一律需确认） */
  readonly isNew: boolean;
  readonly problem: string | null;
}

/** 认证方式：shared 是无需认证（没有请求头）或共享凭据（管理员配的同一份请求头），user_token 个人访问令牌，oauth OAuth 授权。 */
export type ServerAuth = 'shared' | 'user_token' | 'oauth';

/** 个人访问令牌：调用时带「header: prefix + 令牌」；help 是给成员看的「去哪拿令牌」。 */
export interface TokenConfig {
  readonly header: string;
  readonly prefix: string;
  readonly help: string;
}

export const DEFAULT_TOKEN_CONFIG: TokenConfig = { header: 'Authorization', prefix: 'Bearer ', help: '' };

/** OAuth 授权的设置（不含 secret）：dynamic 是自动注册的应用，否则 clientId 是管理员填的。 */
export interface OAuthSettings {
  readonly dynamic: boolean;
  readonly clientId: string;
  readonly hasSecret: boolean;
  /** 管理员填的；空表示用对方建议的 suggestedScope */
  readonly scope: string;
  readonly suggestedScope: string;
  /** 回调地址：对方不支持自动注册时，管理员要填到对方的应用里 */
  readonly redirectUri: string;
}

export interface OAuthInput {
  /** 不填就自动注册 */
  readonly clientId: string;
  /** 修改时同一个 clientId 不填表示沿用原来的 */
  readonly clientSecret: string;
  readonly scope: string;
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
  readonly auth: ServerAuth;
  readonly tokenConfig: TokenConfig | null;
  readonly oauth: OAuthSettings | null;
  /** 企业 MCP 发给哪些空间（"*" 是全部空间）；空间服务为空 */
  readonly audience: readonly string[];
  /** 空间服务已收为企业 MCP 时，那个企业 MCP 的名称 */
  readonly promotedTo: string | null;
}

const group = (value: unknown): ToolGroup => (value === 'read' ? 'read' : 'write');
const policy = (value: unknown): ToolPolicy => (value === 'allow' || value === 'block' ? value : 'ask');

function parseTool(value: unknown): McpTool | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  if (!name) return undefined;
  return {
    name,
    rawName: str(value.rawName) ?? name,
    description: str(value.description) ?? '',
    policy: policy(value.policy),
    group: group(value.group),
    annotatedGroup: group(value.annotatedGroup),
    isNew: bool(value.isNew),
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
    auth: value.auth === 'user_token' || value.auth === 'oauth' ? value.auth : 'shared',
    tokenConfig: parseTokenConfig(value.tokenConfig),
    oauth: parseOAuth(value.oauth),
    audience: listOf(value.audience, (item) => str(item) ?? undefined),
    promotedTo: text(value.promotedTo) ?? null,
  };
}

function parseOAuth(value: unknown): OAuthSettings | null {
  if (!isRecord(value)) return null;
  return {
    dynamic: bool(value.dynamic),
    clientId: str(value.clientId) ?? '',
    hasSecret: bool(value.hasSecret),
    scope: str(value.scope) ?? '',
    suggestedScope: str(value.suggestedScope) ?? '',
    redirectUri: str(value.redirectUri) ?? '',
  };
}

function parseTokenConfig(value: unknown): TokenConfig | null {
  if (!isRecord(value)) return null;
  return {
    header: str(value.header) ?? DEFAULT_TOKEN_CONFIG.header,
    prefix: str(value.prefix) ?? '',
    help: str(value.help) ?? '',
  };
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
  /** 认证方式；修改时 undefined 表示不改 */
  readonly auth?: ServerAuth;
  readonly tokenConfig?: TokenConfig;
  /** OAuth 授权的设置；修改时 undefined 表示不改 */
  readonly oauth?: OAuthInput;
  /** 个人访问令牌时登记人自己的令牌：只用来测试连接、拉工具清单，存成他自己的连接；修改时不填就用他已有的连接 */
  readonly token?: string;
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
  if (input.auth === 'user_token') {
    const config = input.tokenConfig ?? DEFAULT_TOKEN_CONFIG;
    if (!HEADER_NAME.test(config.header.trim()) || RESERVED_HEADERS.has(config.header.trim().toLowerCase()))
      return `请求头 ${config.header} 的名字不合法`;
    if (config.prefix.length > 32) return '前缀最多 32 个字符';
    if (config.help.length > 300) return '给成员的说明最多 300 字';
    if (creating && !input.token?.trim()) return '个人访问令牌方式，要填你自己的令牌来测试连接';
  }
  if (input.auth === 'oauth' && input.oauth) {
    if (input.oauth.clientSecret.trim() && !input.oauth.clientId.trim())
      return '填了 Client Secret 就要填 Client ID';
    if (input.oauth.clientId.length > 200 || input.oauth.scope.length > 500) return 'Client ID 或 scope 过长';
  }
  return null;
}

function headersBody(headers: readonly HeaderInput[]): Record<string, string> {
  return Object.fromEntries(headers.map((h) => [h.name.trim(), h.value.trim()]));
}

function authBody(input: Omit<ServerInput, 'name'>): Record<string, unknown> {
  if (input.auth === undefined) return {};
  if (input.auth === 'shared') return { auth: 'shared' };
  if (input.auth === 'oauth') {
    if (!input.oauth) return { auth: 'oauth' };
    const { clientId, clientSecret, scope } = input.oauth;
    return {
      auth: 'oauth',
      oauth: {
        clientId: clientId.trim(),
        scope: scope.trim(),
        ...(clientSecret.trim() ? { clientSecret: clientSecret.trim() } : {}),
      },
    };
  }
  const config = input.tokenConfig ?? DEFAULT_TOKEN_CONFIG;
  return {
    auth: 'user_token',
    tokenConfig: { header: config.header.trim(), prefix: config.prefix, help: config.help.trim() },
    ...(input.token?.trim() ? { token: input.token.trim() } : {}),
  };
}

export interface Probe {
  readonly oauth: boolean;
  /** 不带凭据请求时对方要凭据（401、403）；false 是公开服务，例如文档检索 */
  readonly needsCredentials: boolean;
  readonly dynamicRegistration: boolean;
  readonly scope: string;
  readonly redirectUri: string;
}

export interface ToolSettings {
  readonly policy?: ToolPolicy;
  readonly group?: ToolGroup;
}

/** 登记请求体（扩展的「收为企业 MCP」也用它）。 */
export function createBody(input: ServerInput): Record<string, unknown> {
  const shared = (input.auth ?? 'shared') === 'shared';
  return {
    name: input.name,
    title: input.title.trim(),
    url: input.url.trim(),
    timeoutSeconds: input.timeoutSeconds,
    headers: shared ? headersBody(input.headers ?? []) : {},
    ...authBody(input),
  };
}

/** 一个范围的 MCP 管理接口：base 拼出这个范围的地址。 */
export function mcpAdminApi(base: (path?: string) => string) {
  return {
    list: async (): Promise<readonly McpServer[]> => listOf(await request<unknown>(base()), parseServer),

    create: async (input: ServerInput): Promise<McpServer | undefined> =>
      parseServer(await request<unknown>(base(), { method: 'POST', body: createBody(input) })),

    update: async (id: string, input: Omit<ServerInput, 'name'>): Promise<McpServer | undefined> => {
      const body: Record<string, unknown> = {
        title: input.title.trim(),
        url: input.url.trim(),
        timeoutSeconds: input.timeoutSeconds,
      };
      if (input.headers !== undefined && input.auth !== 'user_token')
        body.headers = headersBody(input.headers);
      Object.assign(body, authBody(input));
      return parseServer(await request<unknown>(base(`/${seg(id)}`), { method: 'PATCH', body }));
    },

    /** 登记前探一次地址：不要凭据默认无需认证（对方另外支持 OAuth 也一样，授权可选）；要凭据时声明了 OAuth 默认 OAuth 授权，否则个人访问令牌。 */
    probe: async (url: string): Promise<Probe> => {
      const value = await request<unknown>(base('/probe'), { method: 'POST', body: { url: url.trim() } });
      const record = isRecord(value) ? value : {};
      return {
        oauth: bool(record.oauth),
        needsCredentials: bool(record.needsCredentials, true),
        dynamicRegistration: bool(record.dynamicRegistration),
        scope: str(record.scope) ?? '',
        redirectUri: str(record.redirectUri) ?? '',
      };
    },

    setEnabled: (id: string, enabled: boolean): Promise<unknown> =>
      request(base(`/${seg(id)}`), { method: 'PATCH', body: { enabled } }),

    sync: (id: string): Promise<unknown> => request(base(`/${seg(id)}/sync`), { method: 'POST' }),

    remove: (id: string): Promise<void> => request(base(`/${seg(id)}`), { method: 'DELETE' }),

    configureTool: (serverId: string, tool: string, settings: ToolSettings): Promise<unknown> =>
      request(base(`/${seg(serverId)}/tools/${seg(tool)}`), { method: 'PATCH', body: settings }),
  };
}

export type McpAdminApi = ReturnType<typeof mcpAdminApi>;

/** 空间服务的管理接口。 */
export const workspaceMcpApi = mcpAdminApi((path = '') => workspacePath(`/mcp-servers${path}`));
export const {
  list: listServers,
  create: createServer,
  update: updateServer,
  probe: probeServer,
  setEnabled: setServerEnabled,
  sync: syncServer,
  remove: deleteServer,
  configureTool,
} = workspaceMcpApi;

// ---- 到达本空间的企业 MCP（空间管理员停用、收窄给谁用；server/src/knowwork_server/tools/mcp/enterprise.py） ----

export interface EnterpriseHere {
  readonly serverId: string;
  readonly title: string;
  readonly url: string;
  readonly auth: ServerAuth;
  /** 没禁用、能给模型的工具数 */
  readonly tools: number;
  readonly off: boolean;
  /** 收窄了给谁用：只有 resource 上授了「可用」的对象（和管理员）能用；没收窄是全体成员 */
  readonly narrowed: boolean;
  /** 收窄时授权挂的资源（tool:mcp__ent_<名字>__*） */
  readonly resource: string;
}

function parseHere(value: unknown): EnterpriseHere | undefined {
  if (!isRecord(value)) return undefined;
  const serverId = text(value.serverId);
  if (!serverId) return undefined;
  return {
    serverId,
    title: str(value.title) ?? serverId,
    url: str(value.url) ?? '',
    auth: value.auth === 'user_token' || value.auth === 'oauth' ? value.auth : 'shared',
    tools: num(value.tools) ?? 0,
    off: bool(value.off),
    narrowed: bool(value.narrowed),
    resource: str(value.resource) ?? '',
  };
}

const here = (path = '') => workspacePath(`/enterprise-mcp${path}`);

export async function listEnterpriseHere(): Promise<readonly EnterpriseHere[]> {
  const body = await request<unknown>(here());
  return listOf(isRecord(body) ? body.servers : undefined, parseHere);
}

export async function setEnterpriseHereOff(serverId: string, off: boolean): Promise<void> {
  await request<unknown>(here(`/${off ? 'disable' : 'enable'}`), { method: 'POST', body: { serverId } });
}

/** 改在本空间给谁用：含全体成员（role:editor）就是不收窄。 */
export async function setEnterpriseHereUsers(serverId: string, subjects: readonly string[]): Promise<void> {
  await request<unknown>(here('/users'), { method: 'PUT', body: { serverId, subjects } });
}

// ---- 成员自己的连接（「设置 → 连接」） ----

/** ready：不用连接（无需认证、共享凭据）；connected 已连接；invalid 对方拒绝过、要重新连接；none 还没连接 */
export type ConnectionStatus = 'ready' | 'connected' | 'invalid' | 'none';

export interface ConnectionTool {
  readonly name: string;
  readonly description: string;
}

export interface MyConnection {
  readonly serverId: string;
  readonly title: string;
  /** shared 不用连接；user_token 填个人访问令牌；oauth 跳到对方页面授权 */
  readonly auth: ServerAuth;
  /** 本人能用的工具（没禁用、有权限），让成员知道能让 AI 做什么 */
  readonly tools: readonly ConnectionTool[];
  readonly help: string;
  readonly status: ConnectionStatus;
  /** 令牌后四位这类给人看的，不是令牌本身 */
  readonly display: string;
  readonly connectedAt: string | null;
}

function parseConnectionTool(value: unknown): ConnectionTool | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  return name ? { name, description: str(value.description) ?? '' } : undefined;
}

function parseConnection(value: unknown): MyConnection | undefined {
  if (!isRecord(value)) return undefined;
  const serverId = text(value.serverId);
  if (!serverId) return undefined;
  const status =
    value.status === 'ready' || value.status === 'connected' || value.status === 'invalid'
      ? value.status
      : 'none';
  return {
    serverId,
    title: str(value.title) ?? serverId,
    auth: value.auth === 'oauth' || value.auth === 'user_token' ? value.auth : 'shared',
    tools: listOf(value.tools, parseConnectionTool),
    help: str(value.help) ?? '',
    status,
    display: str(value.display) ?? '',
    connectedAt: text(value.connectedAt) ?? null,
  };
}

const mine = (path = '') => workspacePath(`/me/mcp-connections${path}`);

export async function listMyConnections(): Promise<readonly MyConnection[]> {
  return listOf(await request<unknown>(mine()), parseConnection);
}

export async function connectMcp(serverId: string, token: string): Promise<MyConnection | undefined> {
  return parseConnection(
    await request<unknown>(mine(`/${seg(serverId)}`), { method: 'PUT', body: { token: token.trim() } }),
  );
}

export async function disconnectMcp(serverId: string): Promise<void> {
  await request<unknown>(mine(`/${seg(serverId)}`), { method: 'DELETE' });
}

/** OAuth 授权：拿对方的授权地址（浏览器跳过去），授权后回到 returnTo（站内路径），网址上带 mcpConnected 或 mcpError。 */
export async function startMcpLogin(serverId: string, returnTo: string): Promise<string> {
  const value = await request<unknown>(mine(`/${seg(serverId)}/login`), {
    method: 'POST',
    body: { returnTo },
  });
  const url = isRecord(value) ? str(value.authorizeUrl) : undefined;
  if (!url) throw new Error('没有拿到授权地址');
  return url;
}
