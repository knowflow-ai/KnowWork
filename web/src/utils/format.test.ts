import { describe, expect, it } from 'vitest';
import { describeArguments, formatBytes, truncate } from './format';
import { fileKindOf } from '../ui/fileKind';
import { markCitations } from '../features/chat/components/citations';
import { modeForKey } from '../features/chat/components/keys';

describe('format', () => {
  it('formatBytes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(30 * 1024 * 1024)).toBe('30 MB');
    expect(formatBytes(-1)).toBe('未知大小');
  });

  it('truncate 合并空白并截断', () => {
    expect(truncate(' a\n b ', 10)).toBe('a b');
    expect(truncate('一二三四五', 3)).toBe('一二三…');
  });

  it('describeArguments 优先取能说明目标的字段', () => {
    expect(describeArguments('{"content":"x","path":"/a/报告.docx"}')).toBe('path: /a/报告.docx');
    expect(describeArguments('{"n":3}')).toBe('n: 3');
    expect(describeArguments('{}')).toBe('');
    expect(describeArguments('[1,2]')).toBe('1,2');
    expect(describeArguments('not json')).toBe('not json');
    expect(describeArguments('  ')).toBe('');
  });
});

describe('fileKindOf', () => {
  it.each([
    ['a.DOCX', 'doc'],
    ['b.xlsx', 'sheet'],
    ['c.pptx', 'slide'],
    ['d.pdf', 'pdf'],
    ['e.zip', 'other'],
    ['noext', 'other'],
  ])('%s → %s', (name, kind) => expect(fileKindOf(name)).toBe(kind));
});

describe('markCitations', () => {
  it('两种引用标记都改写成内部链接', () => {
    // title 是编号前面那句话（点开时框出原文那一块用）
    expect(markCitations('结论[ID:2]与依据[^10]')).toBe(
      '结论[2](#kw-cite-2 "结论")与依据[10](#kw-cite-10 "结论与依据")',
    );
    expect(markCitations('普通 [链接](http://a)')).toBe('普通 [链接](http://a)');
  });
});

describe('modeForKey', () => {
  const key = (patch: Partial<Parameters<typeof modeForKey>[0]>) => ({
    key: 'Enter',
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    ...patch,
  });
  it('回车发送、Shift 换行、输入法组字不发送', () => {
    expect(modeForKey(key({}), false)).toBe('followup');
    expect(modeForKey(key({ shiftKey: true }), false)).toBeNull();
    expect(modeForKey(key({ isComposing: true }), false)).toBeNull();
    expect(modeForKey(key({ key: 'a' }), false)).toBeNull();
  });
  it('运行中 Ctrl/⌘ + 回车 = 补充到当前任务', () => {
    expect(modeForKey(key({ ctrlKey: true }), true)).toBe('steer');
    expect(modeForKey(key({ metaKey: true }), true)).toBe('steer');
    expect(modeForKey(key({ ctrlKey: true }), false)).toBe('followup');
  });
});
