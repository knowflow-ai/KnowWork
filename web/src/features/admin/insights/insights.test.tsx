import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { register } from '../../../slots/registry';
import InsightsPage from './InsightsPage';
import { changeOf, completionRate, rateChangeOf } from './measures';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const totals = (turns: number, completed: number, failed: number, cancelled: number) => ({
  turns,
  askers: 3,
  completed,
  failed,
  cancelled,
  liked: 4,
  disliked: 2,
  searched: 12,
  cost: '12.4',
  inputTokens: 52000,
  outputTokens: 8000,
});

const INSIGHTS = {
  start: '2026-10-20',
  end: '2026-10-26',
  since: '2026-10-20T00:00:00+08:00',
  until: '2026-10-27T00:00:00+08:00',
  totals: totals(20, 15, 3, 2),
  previous: totals(10, 9, 1, 0),
  days: [
    { date: '2026-10-25', turns: 8, completed: 6, ended: 8 },
    { date: '2026-10-26', turns: 12, completed: 9, ended: 12 },
  ],
  endReasons: [
    { key: 'error', count: 2, status: 'failed' },
    { key: 'blocked', count: 1, status: 'failed' },
    { key: 'aborted', count: 2, status: 'cancelled' },
  ],
  dislikes: [
    { key: 'task-result', count: 1, status: '' },
    { key: '', count: 1, status: '' },
  ],
  uncited: 5,
  tools: [
    { name: 'knowledge_search', turns: 14, failed: 1 },
    { name: 'bash', turns: 3, failed: 0 },
  ],
  bases: [{ id: 'builtin.kb1', name: '制度库', turns: 10, cited: 6, unused: 4, disliked: 1 }],
};

function api() {
  return routeFetch({ [`GET ${B}/insights`]: jsonResponse(INSIGHTS) });
}

/** 链接的查询参数，便于断言 */
const query = (link: HTMLElement) =>
  Object.fromEntries(new URL(link.getAttribute('href') ?? '', 'http://kw').searchParams);
const window = {
  since: '2026-10-20T00:00:00+08:00',
  until: '2026-10-27T00:00:00+08:00',
  from: '2026-10-20',
  to: '2026-10-26',
};

describe('问答大盘', () => {
  it('四个数各和上一段比，需要关注的数点下去是运行记录（带上统计用的时间段与条件）', async () => {
    api();
    renderWithApp(<InsightsPage />);
    const asked = await screen.findByRole('group', { name: '提问' });
    expect(asked).toHaveTextContent('20 轮');
    expect(asked).toHaveTextContent('比上一段多 10 轮');
    // 完成 15 / 已结束 20 = 75%；上一段 9 / 10 = 90%
    expect(screen.getByRole('group', { name: '完成率' })).toHaveTextContent(
      '75%答完 15 · 失败 3 · 取消 2，比上一段低 15 个百分点',
    );
    expect(screen.getByRole('group', { name: '估算费用' })).toHaveTextContent(
      '输入 52,000 · 输出 8,000 token',
    );

    const concerns = screen.getByRole('list', { name: '需要关注' });
    const failed = within(concerns).getByRole('link', { name: '3 轮' });
    expect(query(failed)).toEqual({ ...window, status: 'failed' });
    expect(concerns).toHaveTextContent('出错 2 · 被拦截 1');
    // 失败、取消、被点踩占全部提问（20 轮）；检索了没引用占答完了、检索过知识库的
    expect(concerns).toHaveTextContent('占全部提问 15%');
    expect(concerns).toHaveTextContent('占全部提问 10%');
    expect(concerns).toHaveTextContent('占检索过知识库的 41.7%');
    expect(concerns).toHaveTextContent('用户取消 2');
    expect(concerns).toHaveTextContent('任务结果 1 · 没选分类 1');
    expect(query(within(concerns).getByRole('link', { name: '5 轮' }))).toEqual({
      ...window,
      signal: 'uncited',
    });
  });

  it('工具、知识库两张表的数点下去按工具、按库筛运行记录', async () => {
    api();
    renderWithApp(<InsightsPage />);
    const tools = await screen.findByRole('table', { name: '工具' });
    expect(query(within(tools).getByRole('link', { name: '14 轮' }))).toEqual({
      ...window,
      tool: 'knowledge_search',
    });
    expect(within(tools).getByText('· 7.1%')).toBeInTheDocument(); // 14 轮里失败 1 轮
    expect(query(within(tools).getByRole('link', { name: '1 轮' }))).toEqual({
      ...window,
      tool: 'knowledge_search',
      status: 'failed',
    });
    const bases = screen.getByRole('table', { name: '知识库' });
    expect(within(bases).getByText('60%')).toBeInTheDocument();
    expect(query(within(bases).getByRole('link', { name: '10 轮' }))).toEqual({
      ...window,
      base: 'builtin.kb1',
      baseName: '制度库',
    });
  });

  it('需要关注只列不是 0 的；都是 0 时写一句', async () => {
    const quiet = { ...INSIGHTS, uncited: 0, totals: { ...INSIGHTS.totals, cancelled: 0, disliked: 0 } };
    routeFetch({ [`GET ${B}/insights`]: jsonResponse(quiet) });
    const { unmount } = renderWithApp(<InsightsPage />);
    const concerns = await screen.findByRole('list', { name: '需要关注' });
    expect(
      within(concerns)
        .getAllByRole('listitem')
        .map((item) => item.firstChild?.textContent),
    ).toEqual(['失败']);
    unmount();
    const calm = { ...quiet, totals: { ...quiet.totals, failed: 0 } };
    routeFetch({ [`GET ${B}/insights`]: jsonResponse(calm) });
    renderWithApp(<InsightsPage />);
    expect(await screen.findByText('这段时间没有需要关注的轮次')).toBeInTheDocument();
  });

  it('扩展给某个知识源挂的动作出现在那一行（没注册的知识源不显示）', async () => {
    register('insights.base.action', {
      sourceId: 'builtin',
      label: '去改进',
      to: (kbId) => `/library/${kbId}?tab=improve`,
    });
    routeFetch({
      [`GET ${B}/insights`]: jsonResponse({
        ...INSIGHTS,
        bases: [
          ...INSIGHTS.bases,
          { id: 'kf.ds9', name: '外部库', turns: 1, cited: 0, unused: 1, disliked: 0 },
        ],
      }),
    });
    renderWithApp(<InsightsPage />);
    const bases = await screen.findByRole('table', { name: '知识库' });
    expect(within(bases).getByRole('link', { name: '去改进' })).toHaveAttribute(
      'href',
      '/library/kb1?tab=improve',
    );
    expect(within(bases).getAllByRole('link', { name: '去改进' })).toHaveLength(1);
  });

  it('换时间范围重新取', async () => {
    const calls = api();
    renderWithApp(<InsightsPage />);
    await screen.findByRole('group', { name: '提问' });
    await userEvent.click(screen.getByRole('radio', { name: '最近 30 天' }));
    await waitFor(() => expect(calls.calls.some((c) => c.query.get('days') === '30')).toBe(true));
  });
});

describe('大盘的口径', () => {
  it('完成率只算已结束的；和上一段比', () => {
    expect(completionRate(3, 4)).toBe(0.75);
    expect(completionRate(0, 0)).toBeNull();
    expect(changeOf(12, 9)).toBe('比上一段多 3');
    expect(changeOf(4, 0)).toBe('上一段没有');
    expect(rateChangeOf(0.5, null)).toBe('上一段没有可比的数据');
  });
});
