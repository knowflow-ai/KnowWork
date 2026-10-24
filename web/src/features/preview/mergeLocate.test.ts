/** 一块内容的几处位置合成预览的一个高亮（文档页点分块、对话里点引用共用）。 */
import { describe, expect, it } from 'vitest';
import { mergeLocators } from './mergeLocate';

describe('定位合并', () => {
  it('PDF 的框合在一起、滚到第一处；表格行区间合并；认不出的跳过', () => {
    expect(
      mergeLocators([
        { type: 'pdf', page: 3, quote: '甲', boxes: [{ page: 3, x0: 0, y0: 0, x1: 1, y1: 0.5 }] },
        { type: 'pdf', page: 4, quote: '乙', boxes: [{ page: 4, x0: 0, y0: 0, x1: 1, y1: 0.2 }] },
        { type: 'unknown' },
      ]),
    ).toEqual({
      type: 'pdf',
      page: 3,
      quotes: ['甲', '乙'],
      boxes: [
        { page: 3, x0: 0, y0: 0, x1: 1, y1: 0.5 },
        { page: 4, x0: 0, y0: 0, x1: 1, y1: 0.2 },
      ],
    });
    // Word：几块对上的正文段落区间合起来整段高亮；每块的原文留着，没有区间时按它找
    expect(
      mergeLocators([
        { type: 'docx', block: 0, start: 0, end: 1, quote: '采购合同' },
        { type: 'docx', block: 1, start: 1, end: 4, quote: '合同编号：ZSLDCG2026' },
        { type: 'docx', block: 2, quote: '' },
      ]),
    ).toEqual({
      type: 'docx',
      ranges: [
        { start: 0, end: 1 },
        { start: 1, end: 4 },
      ],
      quotes: ['采购合同', '合同编号：ZSLDCG2026'],
    });
    // PPT 没有框：各处的原文逐段找
    expect(
      mergeLocators([
        { type: 'slide', page: 3, quote: '1.3 交付产物' },
        { type: 'slide', page: 3, quote: '乙方交付的具体产物包括：' },
      ]),
    ).toEqual({ type: 'pdf', page: 3, boxes: [], quotes: ['1.3 交付产物', '乙方交付的具体产物包括：'] });
    expect(
      mergeLocators([
        { type: 'sheet', sheet: '费用', row_start: 5, row_end: 8 },
        { type: 'sheet', sheet: '费用', row_start: 9, row_end: 12 },
        { type: 'sheet', sheet: '别的表', row_start: 1, row_end: 2 },
      ]),
    ).toEqual({ type: 'sheet', sheet: '费用', rowStart: 5, rowEnd: 12 });
    expect(mergeLocators([{ type: 'nothing' }])).toBeUndefined();
  });
});
