import { describe, expect, it } from 'vitest';
import { fileChanges, hasChanges, lineDiff } from './diff';

describe('SKILL.md 逐行对比', () => {
  it('标出删掉与加上的行，其余不变', () => {
    const lines = lineDiff('第一步\n第二步\n第三步', '第一步\n改过的第二步\n第三步\n第四步');
    expect(lines).toEqual([
      { kind: 'same', text: '第一步' },
      { kind: 'del', text: '第二步' },
      { kind: 'add', text: '改过的第二步' },
      { kind: 'same', text: '第三步' },
      { kind: 'add', text: '第四步' },
    ]);
    expect(hasChanges(lines)).toBe(true);
    expect(hasChanges(lineDiff('a\r\nb', 'a\nb'))).toBe(false);
  });

  it('一边为空时整段是增或删', () => {
    expect(lineDiff('', 'a\nb').map((l) => l.kind)).toEqual(['add', 'add']);
    expect(lineDiff('a', '').map((l) => l.kind)).toEqual(['del']);
  });
});

describe('技能包文件的增删改', () => {
  const file = (path: string, sha256: string, size = 1) => ({ path, sha256, size });

  it('按路径与摘要比出新增、删除、改动、未变', () => {
    const rows = fileChanges(
      [file('SKILL.md', 'a'), file('old.md', 'b'), file('ref.md', 'c')],
      [file('SKILL.md', 'a2'), file('ref.md', 'c'), file('new.py', 'd')],
    );
    expect(rows.map((r) => [r.path, r.change])).toEqual([
      ['new.py', 'added'],
      ['old.md', 'removed'],
      ['ref.md', 'same'],
      ['SKILL.md', 'changed'],
    ]);
  });

  it('第一次进空间（没有旧版本）全部算新增', () => {
    expect(fileChanges(null, [file('SKILL.md', 'a')]).map((r) => r.change)).toEqual(['added']);
  });
});
