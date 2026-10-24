/**
 * 按原文在 PDF 文字里找位置，给出要画的框（占页面宽高的比例）。PPT 转出的预览 PDF 没有解析时的坐标，靠它高亮；
 * 有框的定位（PDF 原件）直接用框，不走这里。
 *
 * - 比较时只看字母和数字（loose），空白、标点、大小写都不算；
 * - 原文最多 300 字，取前 60 字找起点，整段对得上就高亮整段，否则只高亮前 60 字；
 * - 先找给定的页（幻灯片序号、PDF 页码），找不到再按页序找；同一句出现多次时取先出现的；
 * - 命中的文字片段整段画框（片段内部不再细分），够看出是哪一句；
 * - 一块由几个版面块组成时（findQuotes），每处的原文依次找，从上一处所在的页往后找，框合在一起。
 */
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { TextItem } from 'pdfjs-dist/types/src/display/api';
import type { Box } from './locate';
import { loose } from './loose';

const PREFIX = 60;
const MIN_NEEDLE = 4;

// 文字内容里还有标记段落结构的项（没有 str），只要真正的文字片段
const isPiece = (item: object): item is TextItem => 'str' in item && 'transform' in item;

export interface QuoteHit {
  readonly page: number;
  readonly boxes: readonly Box[];
}

/** 这一页上要标的框：跨页的块（合并过的表格、跨页的分块）每页都标，滚动只滚到第一处所在的页。 */
export function marksOn(target: QuoteHit | undefined, page: number): readonly Box[] | undefined {
  const boxes = target?.boxes.filter((box) => box.page === page);
  return boxes && boxes.length > 0 ? boxes : undefined;
}

/** 最靠上的那个框（定位后滚到它）；没有框时为 -1。 */
export function topmost(boxes: readonly Box[]): number {
  return boxes.reduce((best, box, index) => (best < 0 || box.y0 < boxes[best]!.y0 ? index : best), -1);
}

/** 在一页的文字片段里找：返回命中片段的下标，找不到返回 undefined。 */
export function matchPieces(pieces: readonly string[], quote: string): readonly number[] | undefined {
  const needle = loose(quote);
  if (needle.length < MIN_NEEDLE) return undefined;
  const owners: number[] = [];
  let hay = '';
  pieces.forEach((piece, index) => {
    const part = loose(piece);
    hay += part;
    for (let i = 0; i < part.length; i += 1) owners.push(index);
  });
  const start = hay.indexOf(needle.slice(0, PREFIX));
  if (start < 0) return undefined;
  const length = hay.startsWith(needle, start) ? needle.length : Math.min(PREFIX, needle.length);
  return [...new Set(owners.slice(start, start + length))];
}

interface Viewport {
  readonly width: number;
  readonly height: number;
  convertToViewportPoint(x: number, y: number): number[];
}

/** 一个文字片段在页面上的框（占页面宽高的比例）。 */
function pieceBox(piece: TextItem, viewport: Viewport, page: number): Box {
  const [, , c = 0, d = 0, x = 0, y = 0] = piece.transform;
  const height = piece.height || Math.hypot(c, d);
  const [x0 = 0, y0 = 0] = viewport.convertToViewportPoint(x, y);
  const [x1 = 0, y1 = 0] = viewport.convertToViewportPoint(x + piece.width, y + height);
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return {
    page,
    x0: clamp(Math.min(x0, x1) / viewport.width),
    y0: clamp(Math.min(y0, y1) / viewport.height),
    x1: clamp(Math.max(x0, x1) / viewport.width),
    y1: clamp(Math.max(y0, y1) / viewport.height),
  };
}

export async function findQuote(
  pdf: PDFDocumentProxy,
  quote: string,
  hint?: number,
): Promise<QuoteHit | undefined> {
  const order = Array.from({ length: pdf.numPages }, (_, i) => i + 1);
  const pages = hint && hint <= pdf.numPages ? [hint, ...order.filter((n) => n !== hint)] : order;
  for (const number of pages) {
    const page = await pdf.getPage(number);
    try {
      const content = await page.getTextContent();
      const pieces = content.items.filter(isPiece);
      const hits = matchPieces(
        pieces.map((p) => p.str),
        quote,
      );
      if (!hits) continue;
      const viewport = page.getViewport({ scale: 1 });
      const boxes = hits.map((index) => pieceBox(pieces[index]!, viewport, number));
      return { page: number, boxes };
    } finally {
      page.cleanup();
    }
  }
  return undefined;
}

/**
 * 几段原文依次找，框合在一起（一块由好几个版面块组成，只找第一处就只高亮了标题）。
 * 每段从上一段命中的页开始找，滚动停在第一处。一段都没找到返回 undefined。
 */
export async function findQuotes(
  pdf: PDFDocumentProxy,
  quotes: readonly string[],
  hint?: number,
): Promise<QuoteHit | undefined> {
  const boxes: Box[] = [];
  let first: number | undefined;
  let near = hint;
  for (const quote of quotes) {
    const hit = await findQuote(pdf, quote, near);
    if (!hit) continue;
    first ??= hit.page;
    near = hit.page;
    boxes.push(...hit.boxes);
  }
  return first === undefined ? undefined : { page: first, boxes };
}
