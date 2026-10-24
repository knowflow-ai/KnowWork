import { describe, expect, it } from 'vitest';
import { bodyElements, marked } from './docxMarks';

/** 照 docx-preview 渲染结果的结构：每节一个 section.docx，正文段落、表格在 article 下。 */
function rendered(...sections: readonly (readonly string[])[]): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = sections
    .map(
      (items) =>
        `<section class="docx"><header><p>页眉不算</p></header><article>${items.join('')}</article></section>`,
    )
    .join('');
  return host;
}

describe('Word 预览找要高亮的段落', () => {
  const host = rendered(
    ['<p>采购合同</p>', '<p></p>', '<p>1.3 交付产物</p>', '<p>部署包 / 源码</p>', '<p>部署文档</p>'],
    ['<p>附件二：售后支持服务清单</p>', '<table><tr><td>服务项目</td></tr></table>'],
  );
  const elements = bodyElements(host);

  it('几节正文接起来按顺序数，页眉页脚不算', () => {
    expect(elements.map((e) => e.tagName.toLowerCase())).toEqual(['p', 'p', 'p', 'p', 'p', 'p', 'table']);
  });

  it('有段落区间按区间取，跨节也行', () => {
    const found = marked(elements, {
      type: 'docx',
      ranges: [
        { start: 2, end: 5 },
        { start: 5, end: 7 },
      ],
      quotes: [],
    });
    expect(found.map((e) => e.textContent)).toEqual([
      '1.3 交付产物',
      '部署包 / 源码',
      '部署文档',
      '附件二：售后支持服务清单',
      '服务项目',
    ]);
  });

  it('没有区间时按每块原文的开头依次找，只比字母和数字', () => {
    const found = marked(elements, {
      type: 'docx',
      ranges: [],
      quotes: ['1.3 交付产物', '附件二：售后支持服务清单', '找不到的话'],
    });
    expect(found.map((e) => e.textContent)).toEqual(['1.3 交付产物', '附件二：售后支持服务清单']);
  });
});
