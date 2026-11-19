import { describe, expect, it, vi } from 'vitest';
import { fileMenus, folderLocation } from './fileMenus';

describe('文件面板的右键菜单（照官方）', () => {
  it('文件：在侧边栏打开、显示位置、默认应用打开、另存为；来源给不出的不列', () => {
    const open = vi.fn(async () => undefined);
    const save = vi.fn(async () => undefined);
    const onOpen = vi.fn();
    const all = fileMenus('c1', { read: async () => new ArrayBuffer(0), open, save }, '/w', onOpen);
    const labels = all.file('out/a.md').map((item) => item.label);
    expect(labels).toHaveLength(4);
    expect(labels[0]).toBe('在侧边栏打开');
    expect(labels[1]).toMatch(/^(在 Finder 中显示|在文件夹显示)$/);
    expect(labels.slice(2)).toEqual(['使用默认应用打开', '另存为…']);
    all.file('out/a.md')[0]?.onSelect();
    expect(onOpen).toHaveBeenCalledWith('out/a.md');
    all.file('out/a.md')[2]?.onSelect();
    expect(open).toHaveBeenCalledWith('c1', 'out/a.md', 'app');
    const bare = fileMenus('c1', { read: async () => new ArrayBuffer(0) }, null, onOpen);
    expect(bare.file('a.md').map((item) => item.label)).toEqual(['在侧边栏打开']);
    expect(bare.folder('out')).toEqual([]);
  });

  it('文件夹：显示位置、复制文件夹路径（按工作文件夹的写法拼）', () => {
    const revealFolder = vi.fn(async () => undefined);
    const menus = fileMenus('c1', { read: async () => new ArrayBuffer(0), revealFolder }, '/w', vi.fn());
    expect(menus.folder('out').map((item) => item.label)[1]).toBe('复制文件夹路径');
    menus.folder('out')[0]?.onSelect();
    expect(revealFolder).toHaveBeenCalledWith('c1', 'out');
    expect(folderLocation('/data/work/', 'out/2026')).toBe('/data/work/out/2026');
    expect(folderLocation('C:\\work', 'out/2026')).toBe('C:\\work\\out\\2026');
    expect(folderLocation('/w', '')).toBe('/w');
  });
});
