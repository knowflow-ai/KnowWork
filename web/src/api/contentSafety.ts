/**
 * 内容安全（敏感词）的策略（server/src/knowwork_server/content_safety/api.py）：每个空间一份，只给空间所有者和管理员。
 * 员工发给模型的内容、模型的回答和它要调用的工具参数，命中就拦下、给一句阻断提示，并记进审计。
 * 保存带上打开时的版本，别的管理员先改过时服务端回 409。规则（拆词、上限）与服务端 policy.py 一致，保存前先在这里校验。
 * 词表有两种：管理员自己填的自定义词表（custom），和扩展登记的现成词表（availableSources，开源版是空的），每个空间勾用哪几种。
 */
import { request } from './http';
import { bool, isRecord, num, str } from './parse';
import { workspacePath } from './workspace';

export const MAX_WORDS = 30_000;
export const MAX_WORDS_BYTES = 1024 * 1024;
export const MAX_WORD_CHARS = 256;
export const MAX_MESSAGE_CHARS = 500;
export const DEFAULT_BLOCK_MESSAGE = '当前对话内容违反公司规范，请修改后重新输入';
export const CUSTOM_SOURCE = 'custom';
/** 上传的 txt 最大多少（规范化以后还要满足 1 MB、3 万个词） */
export const MAX_UPLOAD_BYTES = 4 * 1024 * 1024;

/** 扩展登记的现成词表（例如内置的常见违规词） */
export interface WordSource {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly wordCount: number;
}

export interface ContentSafetyPolicy {
  /** 0 表示这个空间还没保存过（关闭） */
  readonly version: number;
  readonly inputEnabled: boolean;
  readonly outputEnabled: boolean;
  /** 勾了哪几种词表：custom 与扩展登记的来源 */
  readonly wordSources: readonly string[];
  readonly availableSources: readonly WordSource[];
  /** 自定义词表：规范化以后的敏感词，一行一个 */
  readonly words: string;
  readonly wordCount: number;
  readonly blockMessage: string;
  readonly updatedAt: string | null;
}

export interface ContentSafetyInput {
  readonly version: number;
  readonly inputEnabled: boolean;
  readonly outputEnabled: boolean;
  readonly wordSources: readonly string[];
  readonly words: string;
  readonly blockMessage: string;
}

const path = () => workspacePath('/content-safety');

function sourceOf(value: unknown): WordSource | undefined {
  if (!isRecord(value)) return undefined;
  const id = str(value.id);
  if (!id) return undefined;
  return {
    id,
    label: str(value.label) || id,
    description: str(value.description) ?? '',
    wordCount: num(value.word_count) ?? 0,
  };
}

function policyOf(value: unknown): ContentSafetyPolicy {
  const r = isRecord(value) ? value : {};
  const sources = Array.isArray(r.word_sources)
    ? r.word_sources.map(str).filter((id): id is string => !!id)
    : [];
  const available = Array.isArray(r.available_sources) ? r.available_sources.map(sourceOf) : [];
  return {
    version: num(r.version) ?? 0,
    inputEnabled: bool(r.input_enabled),
    outputEnabled: bool(r.output_enabled),
    wordSources: sources.length > 0 ? sources : [CUSTOM_SOURCE],
    availableSources: available.filter((source): source is WordSource => source !== undefined),
    words: str(r.words) ?? '',
    wordCount: num(r.word_count) ?? 0,
    blockMessage: str(r.block_message) || DEFAULT_BLOCK_MESSAGE,
    updatedAt: str(r.updated_at) ?? null,
  };
}

export async function getContentSafety(): Promise<ContentSafetyPolicy> {
  return policyOf(await request<unknown>(path()));
}

export async function saveContentSafety(input: ContentSafetyInput): Promise<ContentSafetyPolicy> {
  const body = {
    version: input.version,
    input_enabled: input.inputEnabled,
    output_enabled: input.outputEnabled,
    word_sources: input.wordSources,
    words: input.words,
    block_message: input.blockMessage.trim(),
  };
  return policyOf(await request<unknown>(path(), { method: 'PUT', body }));
}

/** 下载某份现成词表（txt，一行一个词）的地址 */
export function sourceWordsUrl(sourceId: string): string {
  return `${path()}/sources/${encodeURIComponent(sourceId)}/words`;
}

/** 读出文件的字节：用 FileReader（各浏览器都有；测试环境的 File 没有 arrayBuffer()） */
function bytesOf(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error('文件读不出来'));
    reader.readAsArrayBuffer(file);
  });
}

/**
 * 上传的 txt 读成文字：先按 UTF-8，不是合法的 UTF-8 再按 GB18030（Windows 上中文 txt 常是 GBK 存的），
 * 都不行就当读不了。
 */
export async function readWordsFile(file: File): Promise<string> {
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('文件太大了，最多 4 MB');
  const bytes = await bytesOf(file);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder('gb18030').decode(bytes);
  }
}

/** 拆成一个个词：换行、中英文逗号与分号都算分隔，去掉两头空白与空的，重复的只留一个（同服务端 split_words） */
export function splitWords(raw: string): readonly string[] {
  const words = raw
    .split(/[\r\n,，;；]+/)
    .map((word) => word.trim())
    .filter(Boolean);
  return [...new Set(words)];
}

/** 保存前的校验；返回给人看的问题，没有问题返回 undefined（同服务端 draft_of） */
export function validateContentSafety(input: ContentSafetyInput): string | undefined {
  const words = splitWords(input.words);
  const tooLong = words.find((word) => [...word].length > MAX_WORD_CHARS);
  if (tooLong) return `有一个词超过 ${MAX_WORD_CHARS} 个字：${tooLong.slice(0, 20)}…`;
  if (words.length > MAX_WORDS) return `敏感词最多 ${MAX_WORDS} 个，现在有 ${words.length} 个`;
  if (new TextEncoder().encode(words.join('\n')).length > MAX_WORDS_BYTES) return '敏感词合计不能超过 1 MB';
  const message = input.blockMessage.trim();
  if ([...message].length > MAX_MESSAGE_CHARS) return `阻断提示不能超过 ${MAX_MESSAGE_CHARS} 个字`;
  const enabled = input.inputEnabled || input.outputEnabled;
  if (enabled && input.wordSources.length === 0) return '请至少选一种词表';
  if (enabled && input.wordSources.includes(CUSTOM_SOURCE) && words.length === 0)
    return '用自定义词表就要填敏感词';
  if (enabled && !message) return '开了检查就要填阻断提示';
  return undefined;
}
