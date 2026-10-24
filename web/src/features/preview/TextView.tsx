/**
 * 纯文本预览（Markdown 带定位时也按原文显示，才能标出引用的那段）：有字符区间按区间标，没有就按引用原文找
 * （原文里的空白可能被压成了一个空格，按「空白都算一处」匹配）；标出的那段滚到中间。
 */
import { useEffect, useRef } from 'react';
import type { TextLocate } from './locate';

function range(text: string, focus?: TextLocate): readonly [number, number] | undefined {
  if (!focus) return undefined;
  if (
    focus.start !== undefined &&
    focus.end !== undefined &&
    focus.end > focus.start &&
    focus.end <= text.length
  ) {
    return [focus.start, focus.end];
  }
  const words = focus.quote.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return undefined;
  const pattern = new RegExp(words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+'));
  const found = pattern.exec(text);
  return found ? [found.index, found.index + found[0].length] : undefined;
}

export function TextView({ text, focus }: { readonly text: string; readonly focus?: TextLocate }) {
  const mark = useRef<HTMLElement>(null);
  const hit = range(text, focus);
  const at = hit?.[0];
  useEffect(() => {
    if (at !== undefined) mark.current?.scrollIntoView?.({ block: 'center' });
  }, [at]);
  if (!hit) return <pre className="kw-preview-text">{text}</pre>;
  return (
    <pre className="kw-preview-text">
      {text.slice(0, hit[0])}
      <mark ref={mark} className="kw-preview-mark">
        {text.slice(hit[0], hit[1])}
      </mark>
      {text.slice(hit[1])}
    </pre>
  );
}
