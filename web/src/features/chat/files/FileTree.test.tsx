import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithApp } from '../../../test/render';
import type { DirectoryListing } from '../sources';
import { FilesPane } from './FilesPane';

const LISTINGS: Record<string, DirectoryListing> = {
  '': {
    root: '/data/work',
    path: '',
    truncated: false,
    entries: [
      { name: '报告.docx', type: 'file' },
      { name: 'out', type: 'directory' },
      { name: 'empty', type: 'directory' },
      { name: '.env', type: 'file' },
      { name: 'pipe', type: 'other' },
    ],
  },
  out: {
    root: '/data/work',
    path: 'out',
    truncated: true,
    entries: [{ name: '营收.xlsx', type: 'file' }],
  },
  empty: { root: '/data/work', path: 'empty', truncated: false, entries: [] },
};

function setup() {
  const listeners = new Map<string, () => void>();
  const list = vi.fn(async (_id: string, path: string) => {
    const listing = LISTINGS[path];
    if (!listing) throw new Error('这个位置不在这段对话的工作文件夹里');
    return listing;
  });
  const watch = vi.fn((_id: string, path: string, listener: () => void) => {
    listeners.set(path, listener);
    return () => listeners.delete(path);
  });
  const onOpen = vi.fn();
  const open = vi.fn(async () => undefined);
  const revealFolder = vi.fn(async () => undefined);
  function Harness() {
    const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
    return (
      <FilesPane
        conversationId="c1"
        files={{ read: async () => new ArrayBuffer(0), list, watch, open, revealFolder }}
        expanded={expanded}
        onToggle={(path) =>
          setExpanded(
            (current) =>
              new Set(current.has(path) ? [...current].filter((item) => item !== path) : [...current, path]),
          )
        }
        onOpen={onOpen}
        onClose={() => undefined}
        current="out/营收.xlsx"
      />
    );
  }
  renderWithApp(<Harness />);
  return { list, watch, listeners, onOpen, open, revealFolder };
}

describe('文件面板', () => {
  it('列出工作文件夹：文件夹在前，标题行写工作文件夹的位置，点开头的照样列出，打不开的灰着', async () => {
    setup();
    const tree = await screen.findByRole('tree', { name: '工作文件夹里的文件' });
    const names = (await within(tree).findAllByRole('treeitem')).map((item) => item.textContent);
    expect(names).toEqual(['empty', 'out', '.env', 'pipe', '报告.docx']);
    expect(screen.getByTitle('/data/work')).toBeInTheDocument();
    expect(within(tree).getByRole('button', { name: 'pipe' })).toBeDisabled();
  });

  it('展开一层才列一层，跟着它的变化；空文件夹、被截断的说清楚；点文件交出去，当前文件标出来', async () => {
    const t = setup();
    await userEvent.click(await screen.findByRole('button', { name: 'out' }));
    expect(t.list.mock.calls.map((call) => call[1])).toEqual(['', 'out']);
    expect(await screen.findByText('文件太多，只列出了前 1 个')).toBeInTheDocument();
    const current = screen.getByRole('treeitem', { current: true });
    expect(current).toHaveTextContent('营收.xlsx');
    await userEvent.click(screen.getByRole('button', { name: 'empty' }));
    expect(await screen.findByText('空文件夹')).toBeInTheDocument();
    expect(t.watch.mock.calls.map((call) => call[1])).toEqual(expect.arrayContaining(['', 'out', 'empty']));
    await userEvent.click(within(current).getByRole('button'));
    expect(t.onOpen).toHaveBeenCalledWith('out/营收.xlsx');
    // 根变了：重新列根
    const before = t.list.mock.calls.filter((call) => call[1] === '').length;
    await act(async () => t.listeners.get('')?.());
    expect(t.list.mock.calls.filter((call) => call[1] === '').length).toBeGreaterThan(before);
  });

  it('右键：文件用默认应用打开、文件夹显示位置、工作文件夹复制路径', async () => {
    const t = setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    fireEvent.contextMenu(await screen.findByRole('button', { name: '报告.docx' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '使用默认应用打开' }));
    expect(t.open).toHaveBeenCalledWith('c1', '报告.docx', 'app');
    fireEvent.contextMenu(screen.getByRole('button', { name: 'out' }));
    await userEvent.click((await screen.findAllByRole('menuitem'))[0]!);
    expect(t.revealFolder).toHaveBeenCalledWith('c1', 'out');
    fireEvent.contextMenu(screen.getByTitle('/data/work'));
    await userEvent.click(await screen.findByRole('menuitem', { name: '复制文件夹路径' }));
    expect(writeText).toHaveBeenCalledWith('/data/work');
  });
});
