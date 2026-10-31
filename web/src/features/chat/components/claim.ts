/**
 * 点开引用时高亮哪里：一条引用就是检索到的一块（合并过的结果在交给模型前
 * 已按块拆开），默认整块高亮；只有回答里原样出现了这块里某处的原文——那句话本身，或用引号摘的一段——才收窄到那几处。
 * 只认一字不差（标点、空白、大小写不算），不按相似度挑：改写过的句子照样打开整块，但不能因此选中一处不相干的地方
 * （原来按字面相似度挑，引出句里的产品名每页都有，一份 PPT 点开跳到了不相干的页）。
 */
import type { PreviewLocate } from '../../preview/locate';
import { loose } from '../../preview/loose';
import { mergeLocates } from '../../preview/mergeLocate';
import type { CitationSource } from '../events/types';

/** 片段里一个版面块：预览里的位置与它的原文。 */
export interface Spot {
  readonly locate: PreviewLocate;
  readonly quote: string;
}

// 句子里别的引用编号与图片标记（原样的 [IMG:n:j]，或已改写成链接的「[图](#kw-img-…)」）
const MARKS = /\[(?:ID:|\^)?\d{1,4}\]|\[图\]\([^)]*\)|\[IMG:\d{1,4}:\d{1,3}\]/g;
// 表格一行里的竖线是单元格的边界：同一格里的话才算这句；竖线带进链接的 title 还会让表格把链接从中间切断、露出原文
const BOUNDARY = /[。！？!?\n|]/;
const TRAILING = /[\s。！？!?；;，,、:：]+$/u;
const LEADING = /^\s*(?:[-*+]|\d+[.)、]|#{1,6}|>)\s*/;
const CLAIM_CHARS = 200;

/** 回答里 index 处的编号前面、同一句里的话：去掉别的编号与图片、加粗与列表符号，最多 200 字。 */
export function claimBefore(text: string, index: number): string {
  const head = text.slice(0, Math.max(index, 0)).replace(MARKS, '').replace(TRAILING, '');
  const cut = [...head].reverse().findIndex((ch) => BOUNDARY.test(ch));
  const sentence = cut < 0 ? head : head.slice(head.length - cut);
  return sentence
    .replace(/\*\*|__|`/g, '')
    .replace(LEADING, '')
    .trim()
    .slice(-CLAIM_CHARS);
}

// 回答里用引号摘的原文：够长、在这块里只出现一次才算
const QUOTED = /[“「『"]([^”」』"\n]{8,})[”」』"]/gu;
const MIN_QUOTED = 8;
const MIN_NEEDLE = 4;

function once(text: string, needle: string): boolean {
  const at = text.indexOf(needle);
  return at >= 0 && text.indexOf(needle, at + 1) < 0;
}

/** 回答里用引号摘的、原样在这块里只出现一次的那段原文；没有或不止一段时为 undefined。 */
function quotedExcerpt(spots: readonly Spot[], claim: string): string | undefined {
  const source = loose(spots.map((spot) => spot.quote).join('\n'));
  const found = [...claim.matchAll(QUOTED)]
    .map((match) => match[1] ?? '')
    .filter((quote) => loose(quote).length >= MIN_QUOTED && once(source, loose(quote)));
  return found.length === 1 ? found[0] : undefined;
}

/** 原文里原样出现了回答那句话（或它引号里摘的原文）的那几处；对不上、太短、本来就只有一处时为空（整块高亮）。 */
export function exactSpots(spots: readonly Spot[], claim: string): readonly Spot[] {
  if (spots.length <= 1) return [];
  const needle = loose(quotedExcerpt(spots, claim) ?? claim);
  if (needle.length < MIN_NEEDLE) return [];
  return spots.filter((spot) => loose(spot.quote).includes(needle));
}

/** 回答里原样写了原文时把出处收窄到那几处：高亮位置、页码、摘录都换成那几处的；否则原样（整块）。 */
export function focusOn(
  source: CitationSource | undefined,
  claim: string | undefined,
): CitationSource | undefined {
  const spots = source && claim ? exactSpots(source.spots ?? [], claim) : [];
  const locate = mergeLocates(spots.map((spot) => spot.locate));
  if (!source || !locate) return source;
  const page = locate.type === 'pdf' && locate.page !== undefined ? locate.page : source.page;
  return { ...source, locate, excerpt: spots.map((spot) => spot.quote).join(' '), page };
}
