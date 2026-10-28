/**
 * 会话引用出处（server/src/knowwork_server/knowledge/citation_api.py）。
 *
 * 回答里的 [n] 对应本会话引用目录里的编号；点开角标时显示标题、页码、摘录与「查看原文」。
 * 原文链接由知识源给出（KnowFlow 定位到页），只接受 http(s)，防止 javascript: 之类的链接；
 * 有引用预览器的知识源（插槽 citation.preview）改为在预览抽屉里打开，并按定位高亮：一条引用就是一块，
 * 这一块的几处位置合起来整块高亮（回答里原样写了某处原文时再收窄，见 chat/components/claim.ts）。
 * 回答里的图 [IMG:n:j] 按第 n 条出处的 images 换成图片。
 */
import type { CitationImage, CitationSource } from '../features/chat/events/types';
import { request, seg } from './http';
import { isRecord, listOf, num, text } from './parse';
import { locateOf, type PreviewLocate } from '../features/preview/locate';
import { mergeLocators } from '../features/preview/mergeLocate';
import { workspacePath } from './workspace';

function safeUrl(value: unknown): string | undefined {
  const url = text(value);
  return url && /^https?:\/\//i.test(url) ? url : undefined;
}

/** 图片只认站内地址（服务端签过名）和 http(s)，别的（javascript:、data: 之类）不放行 */
function imageOf(value: unknown): CitationImage | undefined {
  if (!isRecord(value)) return undefined;
  const url = text(value.url);
  if (!url || !(url.startsWith('/api/') || /^https?:\/\//i.test(url))) return undefined;
  return { url, caption: text(value.caption) ?? '' };
}

/** 一块里的一处位置：认得出的定位才要，原文为空的（只有坐标）对不上句子，也不要 */
function spotOf(value: unknown): { locate: PreviewLocate; quote: string } | undefined {
  const locate = locateOf(value);
  const quote = isRecord(value) ? text(value.quote) : undefined;
  return locate && quote ? { locate, quote } : undefined;
}

function parseCitation(value: unknown): readonly [number, CitationSource] | undefined {
  if (!isRecord(value)) return undefined;
  const number = num(value.number);
  const title = text(value.title);
  if (number === undefined || !Number.isInteger(number) || !title) return undefined;
  const images = listOf(value.images, imageOf);
  const spots = listOf(value.locators, spotOf);
  // 摘录截过长度，不拿来定整块的结尾，按每处的原文逐处找
  const whole = Array.isArray(value.locators) ? mergeLocators(value.locators) : undefined;
  return [
    number,
    {
      title,
      excerpt: text(value.excerpt),
      page: num(value.page),
      url: safeUrl(value.openUrl),
      sourceId: text(value.sourceId),
      docRef: text(value.docRef),
      locate: whole ?? locateOf(value.locator),
      ...(spots.length ? { spots } : {}),
      ...(images.length ? { images } : {}),
      ...(text(value.versionNote) ? { versionNote: text(value.versionNote) } : {}),
    },
  ];
}

export async function listCitations(
  conversationId: string,
  signal?: AbortSignal,
): Promise<Readonly<Record<number, CitationSource>>> {
  const body = await request<unknown>(workspacePath(`/conversations/${seg(conversationId)}/citations`), {
    signal,
  });
  const entries = listOf(isRecord(body) ? body.items : undefined, parseCitation);
  return Object.fromEntries(entries);
}
