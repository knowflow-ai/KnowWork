/**
 * 会话引用出处（server/src/knowwork_server/knowledge/citation_api.py）。
 *
 * 回答里的 [n] 对应本会话引用目录里的编号；点开角标时显示标题、页码、摘录与「查看原文」。
 * 原文链接由知识源给出（KnowFlow 定位到页），只接受 http(s)，防止 javascript: 之类的链接。
 */
import type { CitationSource } from '../features/chat/events/types';
import { request, seg } from './http';
import { isRecord, listOf, num, text } from './parse';

function safeUrl(value: unknown): string | undefined {
  const url = text(value);
  return url && /^https?:\/\//i.test(url) ? url : undefined;
}

function parseCitation(value: unknown): readonly [number, CitationSource] | undefined {
  if (!isRecord(value)) return undefined;
  const number = num(value.number);
  const title = text(value.title);
  if (number === undefined || !Number.isInteger(number) || !title) return undefined;
  return [
    number,
    { title, excerpt: text(value.excerpt), page: num(value.page), url: safeUrl(value.openUrl) },
  ];
}

export async function listCitations(
  conversationId: string,
  signal?: AbortSignal,
): Promise<Readonly<Record<number, CitationSource>>> {
  const body = await request<unknown>(`/api/conversations/${seg(conversationId)}/citations`, { signal });
  const entries = listOf(isRecord(body) ? body.items : undefined, parseCitation);
  return Object.fromEntries(entries);
}
