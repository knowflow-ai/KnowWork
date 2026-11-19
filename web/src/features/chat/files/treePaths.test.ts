import { describe, expect, it } from 'vitest';
import { ancestorsOf, childPath, isHidden, sortEntries, workspacePathOf } from './treePaths';

describe('文件面板目录树的路径与排序', () => {
  it('文件夹在前，再按名字的自然顺序、不分大小写', () => {
    const sorted = sortEntries([
      { name: 'b.md', type: 'file' },
      { name: '第10章.docx', type: 'file' },
      { name: 'out', type: 'directory' },
      { name: '第2章.docx', type: 'file' },
      { name: 'A.md', type: 'file' },
      { name: '.git', type: 'directory' },
    ]);
    expect(sorted.map((entry) => entry.name)).toEqual([
      '.git',
      'out',
      'A.md',
      'b.md',
      '第2章.docx',
      '第10章.docx',
    ]);
  });

  it('子项路径、点开头的算隐藏、上级文件夹', () => {
    expect(childPath('', 'out')).toBe('out');
    expect(childPath('out/2026', '报告.docx')).toBe('out/2026/报告.docx');
    expect(isHidden('.env')).toBe(true);
    expect(isHidden('报告.docx')).toBe(false);
    expect(ancestorsOf('a/b/c.md')).toEqual(['a', 'a/b']);
    expect(ancestorsOf('c.md')).toEqual([]);
  });

  it('回答里的各种写法换成相对工作文件夹的路径；不在里面的是 null', () => {
    expect(workspacePathOf('./out/报告.docx', '/data/work')).toBe('out/报告.docx');
    expect(workspacePathOf('out\\报告.docx', null)).toBe('out/报告.docx');
    expect(workspacePathOf('/data/work/out/a.md', '/data/work/')).toBe('out/a.md');
    expect(workspacePathOf('C:\\work\\a.md', 'C:\\work')).toBe('a.md');
    expect(workspacePathOf('/etc/hosts', '/data/work')).toBeNull();
    expect(workspacePathOf('../a.md', '/data/work')).toBeNull();
  });
});
