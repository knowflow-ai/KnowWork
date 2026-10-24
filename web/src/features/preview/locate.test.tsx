import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { locateOf } from './locate';
import { loose } from './loose';
import { findQuotes, marksOn, matchPieces } from './pdfQuote';
import { TextView } from './TextView';

describe('引用定位', () => {
  it('后端的定位换成预览用的四种：PDF（含幻灯片）、Word（按正文段落区间）、表格、文本', () => {
    const box = { page: 2, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 };
    expect(locateOf({ type: 'pdf', page: 2, boxes: [box, { page: 'x' }], quote: '付款' })).toEqual({
      type: 'pdf',
      page: 2,
      boxes: [box],
      quotes: ['付款'],
    });
    expect(locateOf({ type: 'docx', block: 4, start: 30, end: 33, quote: '第三条' })).toEqual({
      type: 'docx',
      ranges: [{ start: 30, end: 33 }],
      quotes: ['第三条'],
    });
    expect(locateOf({ type: 'docx', block: 4, quote: '第三条' })).toEqual({
      type: 'docx',
      ranges: [],
      quotes: ['第三条'],
    });
    expect(locateOf({ type: 'slide', page: 5 })).toEqual({ type: 'pdf', page: 5, boxes: [], quotes: [] });
    expect(locateOf({ type: 'sheet', sheet: '销售', row_start: 3, row_end: 5 })).toEqual({
      type: 'sheet',
      sheet: '销售',
      rowStart: 3,
      rowEnd: 5,
    });
    expect(locateOf({ type: 'sheet' })).toBeUndefined();
    expect(locateOf({ type: 'text', start: 10, end: 20, quote: 'q' })).toEqual({
      type: 'text',
      start: 10,
      end: 20,
      quote: 'q',
    });
    expect(locateOf({ type: 'video' })).toBeUndefined();
    expect(locateOf(null)).toBeUndefined();
  });

  it('在 PDF 文字片段里按原文找：只比字母和数字（空白、标点、大小写不算），整段对不上时只认前 60 字', () => {
    expect(loose('1. 部署包 / 源码 | A')).toBe('1部署包源码a');
    const pieces = ['第二章 报销', '规定', '差旅费用应在 30 天内', '提交 Invoice', '其他'];
    expect(matchPieces(pieces, '差旅费用应在30天内提交 invoice')).toEqual([2, 3]);
    expect(matchPieces(pieces, '报销规定')).toEqual([0, 1]);
    expect(matchPieces(pieces, '找不到的话')).toBeUndefined();
    expect(matchPieces(pieces, '短')).toBeUndefined();
    // 前 60 字一样、后面改过（例如文件小改后再转的 PDF）：只认前 60 字
    const common = `差旅费用应在 30 天内提交，${'按公司标准报销'.repeat(9)}`;
    expect(matchPieces(['前言', `${common}原来写的结尾`], `${common}后来改的结尾`)).toEqual([1]);
  });

  it('文本预览按区间或引用原文标出那一段', () => {
    const text = '第一行\n付款期限  30 天\n第三行';
    const { container, rerender } = render(
      <TextView text={text} focus={{ type: 'text', start: 0, end: 3, quote: '' }} />,
    );
    expect(container.querySelector('mark')?.textContent).toBe('第一行');
    rerender(<TextView text={text} focus={{ type: 'text', quote: '付款期限 30 天' }} />);
    expect(container.querySelector('mark')?.textContent).toBe('付款期限  30 天');
    rerender(<TextView text={text} focus={{ type: 'text', quote: '没有这句' }} />);
    expect(container.querySelector('mark')).toBeNull();
    expect(screen.getByText(/第三行/)).toBeInTheDocument();
  });
});

describe('PDF 上标框', () => {
  it('跨页的块每页都标自己的框，没有框的页不标', () => {
    const top = { page: 3, x0: 0, y0: 0.6, x1: 1, y1: 1 };
    const rest = { page: 4, x0: 0, y0: 0, x1: 1, y1: 0.3 };
    const hit = { page: 3, boxes: [top, rest] };
    expect(marksOn(hit, 3)).toEqual([top]);
    expect(marksOn(hit, 4)).toEqual([rest]);
    expect(marksOn(hit, 5)).toBeUndefined();
    expect(marksOn(undefined, 3)).toBeUndefined();
  });
});

describe('几段原文依次找', () => {
  /** 假 PDF：每页一串文字片段，每个片段占一行（y 从上往下排）。 */
  function fakePdf(pages: readonly (readonly string[])[]) {
    return {
      numPages: pages.length,
      getPage: async (number: number) => ({
        getTextContent: async () => ({
          items: (pages[number - 1] ?? []).map((str, row) => ({
            str,
            width: 50,
            height: 10,
            transform: [1, 0, 0, 10, 0, 100 - row * 10],
          })),
        }),
        getViewport: () => ({
          width: 100,
          height: 100,
          convertToViewportPoint: (x: number, y: number) => [x, 100 - y],
        }),
        cleanup: () => undefined,
      }),
    } as unknown as Parameters<typeof findQuotes>[0];
  }

  it('一块由几处组成：每处都找到、框合在一起，停在第一处所在的页；找不到的跳过', async () => {
    const pdf = fakePdf([['采购合同', '合同编号：ZSLDCG2026'], ['第一条 采购内容']]);
    const hit = await findQuotes(pdf, [
      '采购合同',
      '合同编号：ZSLDCG2026',
      '没有这一句话',
      '第一条 采购内容',
    ]);
    expect(hit?.page).toBe(1);
    expect(hit?.boxes.map((b) => b.page)).toEqual([1, 1, 2]);
    expect(await findQuotes(pdf, ['都找不到的话'])).toBeUndefined();
  });
});
