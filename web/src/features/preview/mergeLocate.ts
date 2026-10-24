/**
 * 一块内容在原件上的几处位置 → 预览的一个高亮定位（知识库文档页点分块、对话里点引用都用它）。
 * 一块可能有好几处（跨页、由几个版面块组成）：
 * - PDF（含转成 PDF 预览的 PPT）：各处的框合在一起；没有框时各处的原文逐段找；滚到第一处所在的页；
 * - Word：几处对上的正文段落区间合起来整段高亮；没有区间的按各处原文找；
 * - 表格：取第一处的工作表，行区间合并；
 * - 纯文本：字符区间合并成一段。
 */
import {
  locateOf,
  type DocxLocate,
  type PdfLocate,
  type PreviewLocate,
  type SheetLocate,
  type TextLocate,
} from './locate';

const defined = (value: number | undefined): value is number => value !== undefined;
const lowest = (values: readonly number[]) => (values.length ? Math.min(...values) : undefined);
const highest = (values: readonly number[]) => (values.length ? Math.max(...values) : undefined);

function mergePdf(first: PdfLocate, all: readonly PreviewLocate[]): PdfLocate {
  const same = all.filter((item): item is PdfLocate => item.type === 'pdf');
  return {
    type: 'pdf',
    page: first.page,
    boxes: same.flatMap((item) => item.boxes),
    quotes: same.flatMap((item) => item.quotes),
  };
}

function mergeDocx(all: readonly PreviewLocate[]): DocxLocate {
  const same = all.filter((item): item is DocxLocate => item.type === 'docx');
  return {
    type: 'docx',
    ranges: same.flatMap((item) => item.ranges),
    quotes: same.flatMap((item) => item.quotes),
  };
}

function mergeSheet(first: SheetLocate, all: readonly PreviewLocate[]): SheetLocate {
  const same = all.filter((item): item is SheetLocate => item.type === 'sheet' && item.sheet === first.sheet);
  return {
    type: 'sheet',
    sheet: first.sheet,
    rowStart: lowest(same.map((item) => item.rowStart).filter(defined)),
    rowEnd: highest(same.map((item) => item.rowEnd ?? item.rowStart).filter(defined)),
  };
}

function mergeText(first: TextLocate, all: readonly PreviewLocate[]): TextLocate {
  const same = all.filter((item): item is TextLocate => item.type === 'text');
  return {
    type: 'text',
    start: lowest(same.map((item) => item.start).filter(defined)),
    end: highest(same.map((item) => item.end).filter(defined)),
    quote: first.quote,
  };
}

/** 已经认出来的几处合成一个；没有时返回 undefined。 */
export function mergeLocates(all: readonly PreviewLocate[]): PreviewLocate | undefined {
  const first = all[0];
  if (!first) return undefined;
  switch (first.type) {
    case 'pdf':
      return mergePdf(first, all);
    case 'docx':
      return mergeDocx(all);
    case 'sheet':
      return mergeSheet(first, all);
    case 'text':
      return mergeText(first, all);
  }
}

/** 接口给的原始定位：认不出的跳过；一个都认不出时返回 undefined（预览照常显示、不高亮）。 */
export function mergeLocators(values: readonly unknown[]): PreviewLocate | undefined {
  return mergeLocates(values.map(locateOf).filter((item): item is PreviewLocate => item !== undefined));
}
