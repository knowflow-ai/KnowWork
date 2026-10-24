/**
 * 文档解析服务（MinerU）的配置（server/src/knowwork_server/docparse/api.py）：只给空间管理员。
 * 会话附件与扩展解析 PDF、Word、PPT 都用它；保存后几秒内生效，不用重启。
 */
import { request, seg } from './http';
import { bool, isRecord, num, str } from './parse';
import { WORKSPACE_ID } from './workspace';

/** 解析模式：auto 按用途（知识库 hybrid、会话附件 pipeline），其余强制，附件与知识库都按它走 */
export type ParserBackend = 'auto' | 'vlm-http-client' | 'hybrid-http-client' | 'pipeline';

export const PARSER_BACKENDS: readonly {
  readonly value: ParserBackend;
  readonly label: string;
  readonly description: string;
}[] = [
  {
    value: 'auto',
    label: '自动',
    description: '填了 VLM 服务地址时知识库用 hybrid，会话附件用 pipeline；没填都用 pipeline',
  },
  {
    value: 'vlm-http-client',
    label: 'VLM',
    description: '整页交给 VLM 识别版面与文字，hybrid 效果不好时用；不做图片解析增强',
  },
  {
    value: 'hybrid-http-client',
    label: 'hybrid',
    description: 'pipeline 结合 VLM，带图片解析增强（给图片写描述、图表还原成数据表）',
  },
  { value: 'pipeline', label: 'pipeline', description: '不用 VLM 服务，精度低一些' },
];

const BACKEND_VALUES = PARSER_BACKENDS.map((b) => b.value);
const needsVlm = (backend: ParserBackend) =>
  backend === 'vlm-http-client' || backend === 'hybrid-http-client';

export interface DocumentParser {
  /** settings：界面里配的；env：部署时的环境变量；none：都没配 */
  readonly source: 'settings' | 'env' | 'none';
  readonly enabled: boolean;
  readonly baseUrl: string;
  /** VLM 推理服务（常见 30000 端口），MinerU 的 vlm、hybrid 模式去访问它；空是没配 */
  readonly serverUrl: string;
  readonly backend: ParserBackend;
  readonly apiKeySet: boolean;
  readonly apiKeyLast4: string | null;
  readonly lang: string;
  readonly maxConcurrency: number;
  readonly updatedAt: string | null;
}

export interface DocumentParserInput {
  readonly enabled: boolean;
  readonly baseUrl: string;
  readonly serverUrl: string;
  readonly backend: ParserBackend;
  /** undefined 保持原来的令牌，空字符串清掉，其余是新令牌 */
  readonly apiKey?: string;
  readonly lang: string;
  readonly maxConcurrency: number;
}

export interface ParserProbe {
  readonly ok: boolean;
  readonly message: string;
  readonly latencyMs: number;
}

const path = () => `/api/workspaces/${seg(WORKSPACE_ID)}/document-parser`;

function parserOf(value: unknown): DocumentParser {
  const r = isRecord(value) ? value : {};
  const source = r.source === 'settings' || r.source === 'env' ? r.source : 'none';
  return {
    source,
    enabled: bool(r.enabled),
    baseUrl: str(r.base_url) ?? '',
    serverUrl: str(r.server_url) ?? '',
    backend: BACKEND_VALUES.find((value) => value === r.backend) ?? 'auto',
    apiKeySet: bool(r.api_key_set),
    apiKeyLast4: str(r.api_key_last4) ?? null,
    lang: str(r.lang) ?? 'ch',
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
    server_url: input.serverUrl.trim(),
    backend: input.backend,
    lang: input.lang,
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

/** VLM 服务地址：VLM、hybrid 模式必填（不然 MinerU 直接报错），其余可以不填；填了要是 http(s) 地址。 */
export function validateServerUrl(value: string, backend: ParserBackend = 'auto'): string | undefined {
  const url = value.trim();
  if (!url) return needsVlm(backend) ? 'VLM、hybrid 模式要填 VLM 服务地址' : undefined;
  return /^https?:\/\/\S+$/.test(url)
    ? undefined
    : '地址要以 http:// 或 https:// 开头，例如 http://127.0.0.1:30000';
}

export function validateParserUrl(value: string): string | undefined {
  const url = value.trim();
  if (!url) return '请填写 MinerU 的地址';
  return /^https?:\/\/\S+$/.test(url)
    ? undefined
    : '地址要以 http:// 或 https:// 开头，例如 http://mineru:8000';
}

/** MinerU 的识别语言（OCR 用），常用的几种。 */
export const PARSER_LANGS: readonly { readonly value: string; readonly label: string }[] = [
  { value: 'ch', label: '中文（含英文）' },
  { value: 'en', label: '英文' },
  { value: 'chinese_cht', label: '繁体中文' },
  { value: 'japan', label: '日文' },
  { value: 'korean', label: '韩文' },
  { value: 'latin', label: '拉丁语系' },
  { value: 'arabic', label: '阿拉伯文' },
  { value: 'cyrillic', label: '西里尔文' },
  { value: 'devanagari', label: '天城文' },
];
