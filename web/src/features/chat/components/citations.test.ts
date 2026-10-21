import { describe, expect, it } from 'vitest';
import { CITE_PREFIX, citedNumbers, markCitations } from './citations';

describe('引用标记', () => {
  it('认 [n]、[ID:n]、[^n]，连写也行', () => {
    expect(markCitations('期限 30 天[1][3]。')).toBe(`期限 30 天[1](${CITE_PREFIX}1)[3](${CITE_PREFIX}3)。`);
    expect(markCitations('a[ID:2] b[^4]')).toBe(`a[2](${CITE_PREFIX}2) b[4](${CITE_PREFIX}4)`);
  });

  it('不碰 Markdown 链接与脚注定义', () => {
    expect(markCitations('[1](http://x)')).toBe('[1](http://x)');
    expect(markCitations('[1]: http://x')).toBe('[1]: http://x');
  });

  it('列出回答里引用的编号（去重、按出现顺序）', () => {
    expect(citedNumbers('a[2] b[1][2] c[ID:5] [9](http://x)')).toEqual([2, 1, 5]);
  });
});
