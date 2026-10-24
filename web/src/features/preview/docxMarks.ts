/**
 * Word 预览（DocxView）里找要高亮的段落：渲染结果里正文段落、表格的顺序，和服务端给的段落区间对得上
 * （commercial parsing/docx_body.py 数法一致）。纯函数，单独放，方便测。
 */
import type { DocxLocate } from './locate';
import { loose } from './loose';

const HEAD = 20;

/** 正文的段落与表格，按文档顺序（几节正文接起来）。 */
export function bodyElements(host: HTMLElement): readonly Element[] {
  return [...host.querySelectorAll('section.docx > article')].flatMap((article) => [...article.children]);
}

/** 要高亮的段落：有区间按区间取；没有就拿每块原文的开头往后依次找。 */
export function marked(elements: readonly Element[], focus: DocxLocate): readonly Element[] {
  if (focus.ranges.length > 0) return focus.ranges.flatMap((r) => elements.slice(r.start, r.end));
  const found: Element[] = [];
  let from = 0;
  for (const quote of focus.quotes) {
    const head = loose(quote).slice(0, HEAD);
    if (head.length < 4) continue;
    const at = elements.findIndex((el, index) => index >= from && loose(el.textContent ?? '').includes(head));
    if (at < 0) continue;
    found.push(elements[at]!);
    from = at + 1;
  }
  return found;
}
