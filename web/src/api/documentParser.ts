/**
 * 文档解析服务（MinerU）的配置（server/src/knowwork_server/docparse/api.py）：全平台共用一份，只给系统管理员。
 * 会话附件与扩展解析 PDF、Word、PPT 都用它；保存后几秒内生效，不用重启。
 */
import { request } from './http';
import { bool, isRecord, num, str } from './parse';

/** 解析档位（MinerU 4.1 的 tier）：auto 让 MinerU 按文档自动推荐，其余强制，附件与知识库都按它走 */
export type ParserTier = 'auto' | 'flash' | 'basic' | 'standard' | 'advanced';

export const PARSER_TIERS: readonly {
  readonly value: ParserTier;
  readonly label: string;
  readonly description: string;
}[] = [
  { value: 'auto', label: '自动', description: 'MinerU 按文档自动推荐档位（标准 → 基础 → 快速 → 高级）' },
  { value: 'flash', label: '快速', description: '最快，适合快速预览与建索引' },
  { value: 'basic', label: '基础', description: 'OCR 与基于模型的解析，资源占用小' },
  { value: 'standard', label: '标准', description: '复杂版面解析得更好，质量与速度均衡' },
  { value: 'advanced', label: '高级', description: '最精细的版面与表格还原，最慢' },
];

const TIER_VALUES = PARSER_TIERS.map((t) => t.value);

export interface DocumentParser {
  /** settings：界面里配的；env：部署时的环境变量；none：都没配 */
  readonly source: 'settings' | 'env' | 'none';
  readonly enabled: boolean;
  readonly baseUrl: string;
  readonly tier: ParserTier;
  readonly apiKeySet: boolean;
  readonly apiKeyLast4: string | null;
  readonly maxConcurrency: number;
  readonly updatedAt: string | null;
}

export interface DocumentParserInput {
  readonly enabled: boolean;
  readonly baseUrl: string;
  readonly tier: ParserTier;
  /** undefined 保持原来的令牌，空字符串清掉，其余是新令牌 */
  readonly apiKey?: string;
  readonly maxConcurrency: number;
}

export interface ParserProbe {
  readonly ok: boolean;
  readonly message: string;
  readonly latencyMs: number;
}

const path = () => '/api/system/document-parser';

function parserOf(value: unknown): DocumentParser {
  const r = isRecord(value) ? value : {};
  const source = r.source === 'settings' || r.source === 'env' ? r.source : 'none';
  return {
    source,
    enabled: bool(r.enabled),
    baseUrl: str(r.base_url) ?? '',
    tier: TIER_VALUES.find((value) => value === r.tier) ?? 'auto',
    apiKeySet: bool(r.api_key_set),
    apiKeyLast4: str(r.api_key_last4) ?? null,
    maxConcurrency: num(r.max_concurrency) ?? 3,
    updatedAt: str(r.updated_at) ?? null,
  };
}

export async function getDocumentParser(): Promise<DocumentParser> {
  return parserOf(await request<unknown>(path()));
}

export async function saveDocumentParser(input: DocumentParserInput): Promise<DocumentParser> {
  const body = {
    enabled: input.enabled,
    base_url: input.baseUrl.trim(),
    tier: input.tier,
    max_concurrency: input.maxConcurrency,
    ...(input.apiKey === undefined ? {} : { api_key: input.apiKey }),
  };
  return parserOf(await request<unknown>(path(), { method: 'PUT', body }));
}

export async function testDocumentParser(baseUrl: string, apiKey?: string): Promise<ParserProbe> {
  const body = await request<unknown>(`${path()}/test`, {
    method: 'POST',
    body: { base_url: baseUrl.trim(), ...(apiKey ? { api_key: apiKey } : {}) },
  });
  const r = isRecord(body) ? body : {};
  return { ok: bool(r.ok), message: str(r.message) ?? '', latencyMs: num(r.latency_ms) ?? 0 };
}

export function validateParserUrl(value: string): string | undefined {
  const url = value.trim();
  if (!url) return '请填写 MinerU 的地址';
  return /^https?:\/\/\S+$/.test(url)
    ? undefined
    : '地址要以 http:// 或 https:// 开头，例如 http://mineru:8000';
}
