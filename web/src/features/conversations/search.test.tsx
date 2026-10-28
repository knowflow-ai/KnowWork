import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import { AppLayout } from '../../layout/AppLayout';
import { useConversationSearch } from '../../stores/conversationSearch';
import { groupByTime, splitHits } from './search';

afterEach(() => {
  vi.unstubAllGlobals();
  useConversationSearch.setState({ open: false });
});

const now = new Date(2026, 9, 28, 15, 0).getTime();
const at = (daysAgo: number) => new Date(now - daysAgo * 24 * 60 * 60 * 1000).toISOString();
const item = (id: string, title: string, daysAgo: number) => ({
  id,
  title,
  createdAt: null,
  updatedAt: at(daysAgo),
});

function api() {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/workspaces/default/me/check': jsonResponse({ allowed: false }),
    'GET /api/workspaces/default/conversations': jsonResponse({
      items: [item('c1', '季度总结', 0), item('c2', '差旅报销', 1), item('c3', '旧会话', 40)],
      next: null,
    }),
    'GET /api/workspaces/default/conversations/search': (call) =>
      jsonResponse({
        items:
          call.query.get('q') === '高铁'
            ? [{ ...item('c2', '差旅报销', 1), snippet: '出差坐高铁一等座能报吗' }]
            : [],
      }),
  });
}

function open() {
  renderWithApp(<AppLayout />, {
    route: '/chat',
    path: '/chat',
    extraRoutes: { '/chat/:id': <p>打开了这个对话</p> },
  });
}

describe('搜索对话（照 ChatGPT）', () => {
  it('侧栏的放大镜打开：没输入时是新对话和按时间分组的最近对话', async () => {
    api();
    open();
    await userEvent.click(await screen.findByRole('button', { name: '搜索对话' }));
    const dialog = await screen.findByRole('dialog', { name: '搜索对话' });
    const options = await within(dialog).findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['新对话', '季度总结', '差旅报销', '旧会话']);
    expect(within(dialog).getByText('今天')).toBeInTheDocument();
    expect(within(dialog).getByText('更早')).toBeInTheDocument();
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('Ctrl/⌘ + K 打开；搜不到直说；输入后列出问过这句话的对话，命中处加粗，回车打开', async () => {
    api();
    open();
    await screen.findByRole('button', { name: '搜索对话' });
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: '搜索对话' });
    const input = within(dialog).getByRole('combobox', { name: '搜索对话' });
    await userEvent.type(input, '没有的词');
    expect(await within(dialog).findByText('没有找到相关的对话')).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, '高铁');
    const option = await within(dialog).findByRole('option', { name: /一等座能报吗/ });
    expect(option).toHaveTextContent('差旅报销出差坐高铁一等座能报吗');
    expect(within(dialog).getAllByRole('option')).toHaveLength(1);
    expect(within(option).getByText('高铁').tagName).toBe('STRONG');
    await userEvent.keyboard('{Enter}');
    expect(await screen.findByText('打开了这个对话')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('分组按今天、昨天、前 7 天、前 30 天、更早；命中处不分大小写切开', () => {
    const groups = groupByTime(
      [item('a', 'A', 0), item('b', 'B', 1), item('c', 'C', 5), item('d', 'D', 90)],
      now,
    );
    expect(groups.map((g) => [g.label, g.items.map((i) => i.id)])).toEqual([
      ['今天', ['a']],
      ['昨天', ['b']],
      ['前 7 天', ['c']],
      ['更早', ['d']],
    ]);
    expect(splitHits('Deep 与 deepseek', 'DEEP')).toEqual([
      { text: 'Deep', hit: true },
      { text: ' 与 ', hit: false },
      { text: 'deep', hit: true },
      { text: 'seek', hit: false },
    ]);
  });
});
