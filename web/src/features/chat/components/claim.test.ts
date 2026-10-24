/**
 * 点开引用高亮哪里：一条引用就是一块，默认整块高亮；回答里原样出现了这块里某处的原文时才收窄到那几处。
 */
import { describe, expect, it } from 'vitest';
import { claimBefore, exactSpots, focusOn } from './claim';
import { markCitations } from './citations';
import type { PreviewLocate } from '../../preview/locate';

const at = (text: string, mark: string) => claimBefore(text, text.indexOf(mark));

describe('引用的那句话', () => {
  it('取编号前面同一句里的话，去掉别的编号、加粗、列表符号和图片', () => {
    const answer =
      '**注意**\n\n- 手动调节时，可动部位不要把手指伸入，以免夹伤[3]。\n- 按住任意按钮 8 秒会停机[2][4]。';
    expect(at(answer, '[3]')).toBe('手动调节时，可动部位不要把手指伸入，以免夹伤');
    expect(at(answer, '[4]')).toBe('按住任意按钮 8 秒会停机');
    expect(at('左右各约 45 度。上下可调。[2]', '[2]')).toBe('上下可调');
    expect(at('见下图[图](#kw-img-1-0)说明书写明了[1]', '[1]')).toBe('见下图说明书写明了');
    expect(at('[1]', '[1]')).toBe('');
  });

  it('改写成链接时把这句话放在 title 上，引号转义', () => {
    expect(markCitations('他说"可以"[1]。')).toBe('他说"可以"[1](#kw-cite-1 "他说\\"可以\\"")。');
  });
});

describe('原样出现了原文才收窄（照 WeKnora）', () => {
  const pdf = (quote: string, page = 11): PreviewLocate => ({
    type: 'pdf',
    page,
    boxes: [],
    quotes: [quote],
  });
  const spots = [
    { locate: pdf('说明'), quote: '说明' },
    {
      locate: pdf('若想设置左右摆头以本体为中心时，可先将摇头座用手调节到左右任何一端', 12),
      quote: '若想设置左右摆头以本体为中心时，可先将摇头座用手调节到左右任何一端',
    },
    {
      locate: pdf('注意 可动部位不可把手指伸入。 可能会使人受伤。'),
      quote: '注意 可动部位不可把手指伸入。 可能会使人受伤。',
    },
  ];

  it('回答里一字不差地写了原文（标点、空白不算），或用引号摘了原文，只标那几处', () => {
    expect(exactSpots(spots, '可动部位不可把手指伸入').map((s) => s.quote)).toEqual([spots[2]!.quote]);
    expect(
      exactSpots(spots, '说明书写着「若想设置左右摆头以本体为中心时」，照做就行').map((s) => s.quote),
    ).toEqual([spots[1]!.quote]);
  });

  it('改写过的、太短的、只有一处的都不收窄：整块高亮，不按相似度去挑，免得挑到不相干的地方', () => {
    expect(exactSpots(spots, '手动调节时，可动部位不要把手指伸入，以免夹伤')).toEqual([]);
    expect(exactSpots(spots, '说明')).toEqual([]);
    expect(exactSpots([spots[2]!], '可动部位不可把手指伸入')).toEqual([]);
    expect(exactSpots(spots, '')).toEqual([]);
  });

  it('收窄后高亮、页码、摘录都换成那几处的；不收窄时原样', () => {
    const source = { title: '说明书', page: 11, locate: pdf('说明'), spots };
    expect(focusOn(source, '说明书写着「若想设置左右摆头以本体为中心时」')).toMatchObject({
      page: 12,
      excerpt: spots[1]!.quote,
      locate: { type: 'pdf', page: 12, quotes: [spots[1]!.quote] },
    });
    expect(focusOn(source, '摆头想以中间为中心')).toBe(source);
  });
});
