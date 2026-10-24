import { describe, expect, it } from 'vitest';
import {
  CITE_PREFIX,
  citedFiles,
  citedNumbers,
  IMAGE_PREFIX,
  markCitations,
  withoutImages,
} from './citations';

describe('引用标记', () => {
  it('认 [n]、[ID:n]、[^n]，连写也行', () => {
    // 链接的 title 是编号前面那句话，点开时按它框出原文那一块（claim.ts）
    expect(markCitations('期限 30 天[1][3]。')).toBe(
      `期限 30 天[1](${CITE_PREFIX}1 "期限 30 天")[3](${CITE_PREFIX}3 "期限 30 天")。`,
    );
    expect(markCitations('a[ID:2] b[^4]')).toBe(`a[2](${CITE_PREFIX}2 "a") b[4](${CITE_PREFIX}4 "a b")`);
    expect(markCitations('[1]')).toBe(`[1](${CITE_PREFIX}1)`);
  });

  it('不碰 Markdown 链接与脚注定义', () => {
    expect(markCitations('[1](http://x)')).toBe('[1](http://x)');
    expect(markCitations('[1]: http://x')).toBe('[1]: http://x');
  });

  it('回答里的图 [IMG:n:j] 改写成图片链接；照抄的整段图片标记归一成编号', () => {
    expect(markCitations('报销分三步[1]。\n[IMG:1:0]')).toBe(
      `报销分三步[1](${CITE_PREFIX}1 "报销分三步")。\n[图](${IMAGE_PREFIX}1-0)`,
    );
    expect(markCitations('<image id="IMG:3:2"><caption>流程图</caption></image>')).toBe(
      `[图](${IMAGE_PREFIX}3-2)`,
    );
  });

  it('流式输出到一半的图片编号先不显示', () => {
    expect(markCitations('见下图\n[IMG:3')).toBe('见下图\n');
    expect(markCitations('见下图\n[IM')).toBe('见下图\n');
    expect(markCitations('数组 a[I')).toBe('数组 a');
  });

  it('复制回答时去掉图片编号和照抄的图片标记', () => {
    expect(
      withoutImages('第一步。\n\n[IMG:1:0]\n\n第二步<image id="IMG:1:1"><caption>x</caption></image>。'),
    ).toBe('第一步。\n\n第二步。');
  });

  it('列出回答里引用的编号（去重、按出现顺序）', () => {
    expect(citedNumbers('a[2] b[1][2] c[ID:5] [9](http://x)')).toEqual([2, 1, 5]);
  });
});

describe('citedFiles', () => {
  it('同一份文档的几处引用合成一项，文件按首次引用排，编号与页码从小到大；目录里没有的编号不算', () => {
    const sources = {
      1: { title: '差旅办法.pdf', sourceId: 'b', docRef: 'kb1/d1', page: 3 },
      2: { title: '采购合同.docx', sourceId: 'b', docRef: 'kb1/d2' },
      3: { title: '差旅办法.pdf', sourceId: 'b', docRef: 'kb1/d1', page: 5 },
      4: { title: '外部制度', url: 'http://kf/doc' },
    };
    const files = citedFiles('先看[2]，再看[3][1]，外部[4]，没有的[9]，重复[2]。', sources);
    expect(files.map((f) => [f.title, f.numbers, f.pages])).toEqual([
      ['采购合同.docx', [2], []],
      ['差旅办法.pdf', [1, 3], [3, 5]],
      ['外部制度', [4], []],
    ]);
    expect(files[1]?.first.page).toBe(5);
    expect(citedFiles('没有引用', sources)).toEqual([]);
  });
});
