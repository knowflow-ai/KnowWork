/**
 * 输入框 @ 的候选与发送格式（server conversations/mention_api.py、conversations/mentions.py）。
 *
 * 六种：知识库、目录（发送时展开成目录下的知识库）、文件、标签（都带所在知识库）、MCP 服务、技能。
 * 限定检索只认 id，名字只用来显示和告诉模型。@ 的 MCP 服务只是「这一轮优先用它的工具」，不代表授权。
 */
import { request } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

export type MentionType = 'knowledge_base' | 'folder' | 'document' | 'tag' | 'mcp' | 'skill';

/** 选中的一个 @。id 按类型：知识库全局 id、目录路径、文档引用、标签、MCP 服务标识、技能名。 */
export interface Mention {
  readonly type: MentionType;
  readonly id: string;
  readonly name: string;
  /** 文件、标签所在的知识库（全局 id 与名字） */
  readonly base?: string;
  readonly baseName?: string;
  /** 目录下的知识库 */
  readonly bases?: readonly string[];
}

export interface MentionBase {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly source: string;
  readonly path: readonly string[];
  readonly tags: readonly string[];
  /** 能 @ 库里的文件、标签 */
  readonly pinnable: boolean;
}

export interface MentionSkill {
  readonly name: string;
  readonly description: string;
}

/** 能 @ 的 MCP 服务：工具已经在对话里的（不用连接的、已连接的）。 */
export interface MentionService {
  /** 服务标识，工具名前缀 mcp__标识__ 里那段 */
  readonly name: string;
  readonly title: string;
  readonly tools: number;
  readonly status: 'ready' | 'connected' | 'invalid';
}

export interface MentionDocument {
  readonly id: string;
  readonly title: string;
  readonly base: string;
  readonly baseName: string;
}

export interface MentionOptions {
  readonly bases: readonly MentionBase[];
  readonly services: readonly MentionService[];
  readonly skills: readonly MentionSkill[];
}

const base = (path = '') => workspacePath(`/conversations/mention-options${path}`);

function parseBase(value: unknown): MentionBase | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  return {
    id,
    name: text(value.name) ?? id,
    description: str(value.description) ?? '',
    source: str(value.source) ?? '',
    path: strings(value.path),
    tags: strings(value.tags),
    pinnable: bool(value.pinnable),
  };
}

function parseSkill(value: unknown): MentionSkill | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  return name ? { name, description: str(value.description) ?? '' } : undefined;
}

function parseService(value: unknown): MentionService | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  if (!name) return undefined;
  const status = value.status === 'connected' || value.status === 'invalid' ? value.status : 'ready';
  return { name, title: text(value.title) ?? name, tools: num(value.tools) ?? 0, status };
}

function parseDocument(value: unknown): MentionDocument | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const base = text(value.base);
  if (!id || !base) return undefined;
  return { id, title: text(value.title) ?? id, base, baseName: str(value.base_name) ?? base };
}

export async function getMentionOptions(): Promise<MentionOptions> {
  const data = await request<unknown>(base());
  const body = isRecord(data) ? data : {};
  return {
    bases: listOf(body.bases, parseBase),
    services: listOf(body.services, parseService),
    skills: listOf(body.skills, parseSkill),
  };
}

export async function findMentionDocuments(query: string): Promise<readonly MentionDocument[]> {
  const data = await request<unknown>(base('/documents'), { query: { q: query } });
  return listOf(isRecord(data) ? data.documents : undefined, parseDocument);
}

/** 发送时的写法（SendMessage.mentions）。 */
export function mentionBody(mention: Mention): Record<string, unknown> {
  return {
    type: mention.type,
    id: mention.id,
    name: mention.name,
    ...(mention.base ? { base: mention.base, base_name: mention.baseName ?? mention.base } : {}),
    ...(mention.bases?.length ? { bases: mention.bases } : {}),
  };
}

/** 同一项只留一个：知识库、技能按 id，文件按文档，标签按「库 + 标签」，目录按路径。 */
export function mentionKey(mention: Mention): string {
  return `${mention.type}:${mention.base ?? ''}:${mention.id}`;
}
