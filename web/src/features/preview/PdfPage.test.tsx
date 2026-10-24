/**
 * 没画的页按这份 PDF 的页面比例占位：占位和画出来的一样高，画出来时不会把下面的页顶上去、顶偏刚滚到的位置
 * （原来一律按竖版 3:4 占位，横版幻灯片画出来缩掉一大截，引用点开后停在了高亮区的底部）。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { PdfPage } from './PdfPage';
import { topmost } from './pdfQuote';

// 页面一直取不回来：停在占位
const pending = { getPage: () => new Promise(() => undefined) } as unknown as PDFDocumentProxy;

function placeholder(ratio?: number) {
  render(
    <PdfPage
      pdf={pending}
      page={3}
      requested={false}
      signal={new AbortController().signal}
      zoom={1}
      ratio={ratio}
    />,
  );
  return screen.getByLabelText('第 3 页加载中');
}

describe('PDF 页的占位', () => {
  it('按页面的高宽比留高度（横版幻灯片 16:9）', () => {
    expect(placeholder(9 / 16)).toHaveStyle({ aspectRatio: `1 / ${9 / 16}` });
  });

  it('不知道比例时不写死，交给样式表的默认值', () => {
    expect(placeholder().style.aspectRatio).toBe('');
  });
});

describe('定位后滚到哪', () => {
  it('滚到这一页最靠上的那个框（几处原文找到的先后不一定是从上到下）', () => {
    const box = (y0: number) => ({ page: 1, x0: 0.1, y0, x1: 0.9, y1: y0 + 0.05 });
    expect(topmost([box(0.6), box(0.1), box(0.4)])).toBe(1);
    expect(topmost([box(0.3)])).toBe(0);
    expect(topmost([])).toBe(-1);
  });
});
