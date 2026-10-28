/** 模型厂商、模型、连通测试与用量（server/src/knowwork_server/models/api.py）。
 * 厂商与模型全平台共用，接口在 /api/system 下、只给系统管理员；用量按空间。 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

const base = (path: string) => workspacePath(path);
const system = (path: string) => `/api/system${path}`;

export const CAPABILITIES = [
  { value: 'chat', label: '对话' },
  { value: 'embedding', label: '向量' },
  { value: 'rerank', label: '重排序' },
  { value: 'vision', label: '看图' },
] as const;

export function capabilityLabel(value: string): string {
  return CAPABILITIES.find((c) => c.value === value)?.label ?? value;
}

export interface Provider {
  readonly id: string;
  readonly name: string;
  readonly kind: string;
  readonly baseUrl: string;
  readonly apiKeyConfigured: boolean;
  readonly apiKeyLast4: string;
  readonly enabled: boolean;
}

function parseProvider(value: unknown): Provider | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    kind: str(value.kind) ?? '',
    baseUrl: str(value.base_url) ?? '',
    apiKeyConfigured: bool(value.api_key_configured),
    apiKeyLast4: str(value.api_key_last4) ?? '',
    enabled: bool(value.enabled, true),
  };
}

export async function listProviders(): Promise<readonly Provider[]> {
  return listOf(await request<unknown>(system('/model-providers')), parseProvider);
}

export interface ProviderInput {
  readonly name: string;
  readonly baseUrl: string;
  /** 新建时为空表示不设密钥；修改时 undefined 表示不改，空串表示清除。 */
  readonly apiKey?: string;
  readonly enabled: boolean;
}

const URL_PATTERN = /^https?:\/\/[^\s/?#@]+(\/[^\s?#]*)?$/;

export function validateBaseUrl(value: string): string | null {
  const url = value.trim();
  if (!url) return '请输入地址';
  if (url.length > 500 || !URL_PATTERN.test(url))
    return '地址须为 http(s):// 开头，不带账号密码、查询串或锚点';
  return null;
}

export function createProvider(input: ProviderInput): Promise<unknown> {
  return request(system('/model-providers'), {
    method: 'POST',
    body: {
      name: input.name.trim(),
      base_url: input.baseUrl.trim(),
      api_key: input.apiKey ? input.apiKey : null,
      enabled: input.enabled,
    },
  });
}

export function updateProvider(id: string, input: ProviderInput): Promise<unknown> {
  const body: Record<string, unknown> = {
    name: input.name.trim(),
    base_url: input.baseUrl.trim(),
    enabled: input.enabled,
  };
  if (input.apiKey !== undefined) body.api_key = input.apiKey;
  return request(system(`/model-providers/${seg(id)}`), { method: 'PATCH', body });
}

export function deleteProvider(id: string): Promise<void> {
  return request(system(`/model-providers/${seg(id)}`), { method: 'DELETE' });
}

/** 对话模型的思考：开、关；null 是跟随服务默认（不补参数） */
export type Thinking = 'on' | 'off' | null;

const thinkingOf = (value: unknown): Thinking => (value === 'on' || value === 'off' ? value : null);

export interface Model {
  readonly id: string;
  readonly providerId: string;
  readonly alias: string;
  readonly name: string;
  readonly capabilities: readonly string[];
  readonly contextLength: number | null;
  readonly thinking: Thinking;
  readonly inputPrice: number | null;
  readonly outputPrice: number | null;
  readonly currency: string;
  readonly enabled: boolean;
}

function parseModel(value: unknown): Model | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const alias = text(value.alias);
  if (!id || !alias) return undefined;
  return {
    id,
    providerId: str(value.provider_id) ?? '',
    alias,
    name: str(value.name) ?? alias,
    capabilities: strings(value.capabilities),
    contextLength: num(value.context_length) ?? null,
    thinking: thinkingOf(value.thinking),
    inputPrice: num(value.input_price_per_mtok) ?? null,
    outputPrice: num(value.output_price_per_mtok) ?? null,
    currency: str(value.currency) ?? 'CNY',
    enabled: bool(value.enabled, true),
  };
}

export async function listModels(): Promise<readonly Model[]> {
  return listOf(await request<unknown>(system('/models')), parseModel);
}

export interface ModelInput {
  readonly providerId: string;
  readonly alias: string;
  readonly name: string;
  readonly capabilities: readonly string[];
  readonly contextLength: number | null;
  readonly thinking: Thinking;
  readonly inputPrice: number | null;
  readonly outputPrice: number | null;
  readonly enabled: boolean;
}

export const ALIAS_PATTERN = /^[A-Za-z0-9_.:-]{1,64}$/;

function modelBody(input: ModelInput): Record<string, unknown> {
  return {
    alias: input.alias.trim(),
    name: input.name.trim(),
    capabilities: input.capabilities,
    context_length: input.contextLength,
    thinking: input.thinking,
    input_price_per_mtok: input.inputPrice,
    output_price_per_mtok: input.outputPrice,
    enabled: input.enabled,
  };
}

export function createModel(input: ModelInput): Promise<unknown> {
  return request(system('/models'), {
    method: 'POST',
    body: { provider_id: input.providerId, ...modelBody(input) },
  });
}

export function updateModel(id: string, input: ModelInput): Promise<unknown> {
  return request(system(`/models/${seg(id)}`), { method: 'PATCH', body: modelBody(input) });
}

export function deleteModel(id: string): Promise<void> {
  return request(system(`/models/${seg(id)}`), { method: 'DELETE' });
}

export interface ProbeResult {
  readonly ok: boolean;
  readonly latencyMs: number | null;
  readonly message: string;
}

export async function testModel(id: string): Promise<ProbeResult> {
  const body = await request<unknown>(system(`/models/${seg(id)}/test`), { method: 'POST' });
  if (!isRecord(body)) return { ok: false, latencyMs: null, message: '测试结果不完整' };
  return {
    ok: bool(body.ok),
    latencyMs: num(body.latency_ms) ?? null,
    message: str(body.message) ?? '',
  };
}

// ---- 用量 ----

export type UsageGroupBy = 'user' | 'model' | 'day';

export interface UsageRow {
  readonly key: string;
  readonly calls: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
  readonly cost: number;
}

function parseUsage(value: unknown): UsageRow | undefined {
  if (!isRecord(value)) return undefined;
  const key = str(value.key);
  if (key === undefined) return undefined;
  return {
    key,
    calls: num(value.calls) ?? 0,
    inputTokens: num(value.input_tokens) ?? 0,
    outputTokens: num(value.output_tokens) ?? 0,
    totalTokens: num(value.total_tokens) ?? 0,
    cost: num(value.cost) ?? 0,
  };
}

export interface UsageQuery {
  readonly start: string;
  readonly end: string;
  readonly groupBy: UsageGroupBy;
}

export async function fetchUsage(query: UsageQuery): Promise<readonly UsageRow[]> {
  const body = await request<unknown>(base('/usage'), {
    query: { start: query.start, end: query.end, group_by: query.groupBy },
  });
  return listOf(body, parseUsage);
}

export async function fetchMyUsage(query: UsageQuery): Promise<readonly UsageRow[]> {
  const body = await request<unknown>(base('/usage/me'), {
    query: { start: query.start, end: query.end, group_by: query.groupBy },
  });
  return listOf(body, parseUsage);
}
