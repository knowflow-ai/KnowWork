/** 知识源与知识库列表（server/src/knowwork_server/knowledge/api.py）。 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

const base = (path = '') => workspacePath(`/knowledge-sources${path}`);

/**
 * 认证方式：统一账号是主用的；签名断言要 KnowFlow 先支持，新建时不提供，
 * 只在修改还在用它的知识源时出现，方便改成统一账号。
 */
export type AuthMode = 'shared' | 'assertion';
export const AUTH_MODES: readonly { readonly value: AuthMode; readonly label: string }[] = [
  { value: 'shared', label: '统一账号' },
  { value: 'assertion', label: '签名断言（KnowFlow 暂不支持）' },
];

export const MIN_SERVICE_KEY_LENGTH = 32;
/** 统一账号的 KnowFlow API Key。 */
export const MIN_ACCOUNT_KEY_LENGTH = 8;

export interface SecretStatus {
  readonly configured: boolean;
  readonly last4: string;
}

export interface SourceConfig {
  readonly authMode: AuthMode;
  readonly datasetIds: readonly string[];
  readonly webBaseUrl: string | null;
  readonly similarityThreshold: number;
  readonly vectorSimilarityWeight: number;
  readonly timeoutSeconds: number;
}

export interface KnowledgeSource {
  readonly id: string;
  readonly name: string;
  readonly kind: string;
  readonly enabled: boolean;
  readonly authMode: AuthMode;
  /** 只有可编辑的人才拿得到下面这些。 */
  readonly detail: {
    readonly baseUrl: string;
    readonly config: SourceConfig;
    /** 签名断言方式的服务密钥。 */
    readonly serviceKey: SecretStatus;
    /** 统一账号方式的 KnowFlow API Key。 */
    readonly accountKey: SecretStatus;
  } | null;
}

function parseSecret(value: unknown): SecretStatus | null {
  if (!isRecord(value)) return null;
  return { configured: bool(value.configured), last4: str(value.last4) ?? '' };
}

const authMode = (value: unknown): AuthMode => (value === 'assertion' ? 'assertion' : 'shared');
const NOT_SET: SecretStatus = { configured: false, last4: '' };

function parseConfig(value: unknown): SourceConfig {
  const v = isRecord(value) ? value : {};
  return {
    authMode: authMode(v.auth_mode),
    datasetIds: strings(v.dataset_ids),
    webBaseUrl: text(v.web_base_url) ?? null,
    similarityThreshold: num(v.similarity_threshold) ?? 0.2,
    vectorSimilarityWeight: num(v.vector_similarity_weight) ?? 0.3,
    timeoutSeconds: num(v.timeout_seconds) ?? 15,
  };
}

function parseSource(value: unknown): KnowledgeSource | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  const editable = typeof value.base_url === 'string';
  return {
    id,
    name,
    kind: str(value.kind) ?? 'knowflow',
    enabled: bool(value.enabled, true),
    authMode: authMode(value.auth_mode),
    detail: editable
      ? {
          baseUrl: str(value.base_url) ?? '',
          config: parseConfig(value.config),
          serviceKey: parseSecret(value.service_key) ?? NOT_SET,
          accountKey: parseSecret(value.account_key) ?? NOT_SET,
        }
      : null,
  };
}

export async function listSources(): Promise<readonly KnowledgeSource[]> {
  return listOf(await request<unknown>(base()), parseSource);
}

export interface SourceInput {
  readonly name: string;
  readonly baseUrl: string;
  readonly enabled: boolean;
  readonly config: SourceConfig;
  /** 不填表示不改（修改时）或不设（新建时）。只在签名断言方式下提交。 */
  readonly serviceKey?: string;
  /** 统一账号的 KnowFlow API Key，规则同上，只在统一账号方式下提交。 */
  readonly accountKey?: string;
}

/** 修改时知识源现在的认证方式，以及这种方式的密钥是否已配置（换了方式，旧密钥就不算数）。 */
export interface StoredSecret {
  readonly authMode: AuthMode;
  readonly configured: boolean;
}

const DATASET_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function validateSourceInput(input: SourceInput, stored?: StoredSecret): string | null {
  const name = input.name.trim();
  if (!name || name.length > 100) return '名称须为 1 到 100 个字';
  if (!/^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/.test(input.baseUrl.trim())) {
    return '地址必须是 http(s):// 开头的完整地址，且不带查询参数';
  }
  const mode = input.config.authMode;
  if (input.config.datasetIds.some((id) => !DATASET_ID.test(id))) return '知识库 ID 格式不合法';
  const kept = stored?.authMode === mode && stored.configured;
  if (mode === 'assertion') {
    if (input.serviceKey && input.serviceKey.length < MIN_SERVICE_KEY_LENGTH) {
      return `服务密钥至少 ${MIN_SERVICE_KEY_LENGTH} 个字符`;
    }
    if (!input.serviceKey && !kept) return '签名断言方式必须配置服务密钥';
  }
  if (mode === 'shared') {
    if (input.accountKey && input.accountKey.length < MIN_ACCOUNT_KEY_LENGTH) {
      return `KnowFlow API Key 至少 ${MIN_ACCOUNT_KEY_LENGTH} 个字符`;
    }
    if (!input.accountKey && !kept) return '统一账号方式必须配置 KnowFlow API Key';
  }
  return null;
}

function sourceBody(input: SourceInput): Record<string, unknown> {
  const c = input.config;
  const body: Record<string, unknown> = {
    name: input.name.trim(),
    base_url: input.baseUrl.trim(),
    enabled: input.enabled,
    config: {
      auth_mode: c.authMode,
      dataset_ids: c.datasetIds,
      web_base_url: c.webBaseUrl?.trim() || null,
      similarity_threshold: c.similarityThreshold,
      vector_similarity_weight: c.vectorSimilarityWeight,
      timeout_seconds: c.timeoutSeconds,
    },
  };
  // 只提交当前方式用的密钥：表单里切换过方式时，另一种方式填过的值不带上（服务端会拒绝不符的密钥）
  if (c.authMode === 'assertion' && input.serviceKey) body.service_key = input.serviceKey;
  if (c.authMode === 'shared' && input.accountKey) body.account_key = input.accountKey;
  return body;
}

export function createSource(input: SourceInput): Promise<unknown> {
  // 新建接口不收 enabled 字段（新建的知识源总是启用）
  const body = Object.fromEntries(Object.entries(sourceBody(input)).filter(([key]) => key !== 'enabled'));
  return request(base(), { method: 'POST', body: { ...body, kind: 'knowflow' } });
}

export function updateSource(id: string, input: SourceInput): Promise<unknown> {
  return request(base(`/${seg(id)}`), { method: 'PATCH', body: sourceBody(input) });
}

export function deleteSource(id: string): Promise<void> {
  return request(base(`/${seg(id)}`), { method: 'DELETE' });
}

export async function testSource(id: string): Promise<{ readonly ok: boolean; readonly message: string }> {
  const body = await request<unknown>(base(`/${seg(id)}/test`), { method: 'POST' });
  return isRecord(body)
    ? { ok: bool(body.ok), message: str(body.message) ?? '' }
    : { ok: false, message: '测试结果不完整' };
}

// ---- 知识库 ----

export interface KnowledgeBase {
  /** 全局 id：<知识源id>.<知识库id>，授权写成 knowledge_base:<全局 id>。 */
  readonly id: string;
  readonly kbId: string;
  readonly name: string;
}

export interface SourceBases {
  readonly sourceId: string;
  readonly sourceName: string;
  readonly authMode: AuthMode;
  readonly enabled: boolean;
  readonly bases: readonly KnowledgeBase[];
  /** 列不出来时的原因；开放范围里的知识库仍然列着（名字用 id）。 */
  readonly error: string | null;
}

function parseBase(value: unknown): KnowledgeBase | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const kbId = text(value.kb_id);
  return id && kbId ? { id, kbId, name: text(value.name) ?? kbId } : undefined;
}

function parseSourceBases(value: unknown): SourceBases | undefined {
  if (!isRecord(value)) return undefined;
  const sourceId = text(value.source_id);
  if (!sourceId) return undefined;
  return {
    sourceId,
    sourceName: text(value.source_name) ?? sourceId,
    authMode: authMode(value.auth_mode),
    enabled: bool(value.enabled, true),
    bases: listOf(value.bases, parseBase),
    error: text(value.error) ?? null,
  };
}

/** 可编辑的知识源下各有哪些知识库（授权页的树）。以当前管理员身份去列，统一账号方式用那个账号。 */
export async function listKnowledgeBases(): Promise<readonly SourceBases[]> {
  return listOf(await request<unknown>(workspacePath('/knowledge-bases')), parseSourceBases);
}
