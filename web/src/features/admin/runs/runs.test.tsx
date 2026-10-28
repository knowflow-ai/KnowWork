import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import RunsPage from './RunsPage';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const ROW = {
  conversationId: 'c1',
  title: '请假条',
  userId: 'u1',
  userName: '李雷',
  turn: 2,
  startedAt: '2026-10-20T10:00:00+00:00',
  status: 'failed',
  durationMs: 4200,
  modelCalls: 3,
  inputTokens: 1500,
  outputTokens: 30,
  cost: null,
  question: '请假要提前几天',
  tools: ['knowledge_search'],
  searches: 2,
  cited: 0,
  rating: 'dislike',
};

function api() {
  return routeFetch({
    [`GET ${B}/runs`]: jsonResponse({ items: [ROW], next: null }),
    [`GET ${B}/members`]: jsonResponse([
      { user_id: 'u1', role: 'editor', name: '李雷', email: 'li@corp.cn' },
    ]),
    [`GET ${B}/runs/c1/turns/2`]: jsonResponse({
      summary: {
        turn: 2,
        status: 'failed',
        request: '导出 word',
        steps: 0,
        toolCalls: 0,
        errors: 1,
        inputTokens: 0,
        outputTokens: 0,
        cost: null,
        durationMs: 4200,
        startedMs: 1,
      },
      steps: [],
      subagents: [],
      events: [],
    }),
  });
}

describe('RunsPage', () => {
  it('列出每一轮：成员、提问、状态、耗时、知识库检索与引用、反馈与用量；点开看运行详情（走管理端接口）', async () => {
    const calls = api();
    renderWithApp(<RunsPage />);
    expect(await screen.findByText('请假要提前几天')).toBeInTheDocument();
    expect(screen.getByText('请假条 · 第 2 轮')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('4.2 秒')).toBeInTheDocument();
    expect(screen.getByText('检索 2 次 · 引用 0 条')).toBeInTheDocument();
    expect(screen.getByText('踩')).toBeInTheDocument();
    expect(calls.find('GET', `${B}/runs`)[0]?.query.get('since')).toBeTruthy();
    await userEvent.click(screen.getByText('请假要提前几天'));
    expect(await screen.findByText(/请假条 · 第 2 轮/)).toBeInTheDocument();
    await waitFor(() => expect(calls.find('GET', `${B}/runs/c1/turns/2`)).toHaveLength(1));
  });

  it('按状态、信号筛选时带上参数', async () => {
    const calls = api();
    renderWithApp(<RunsPage />);
    await screen.findByText('请假要提前几天');
    await pickOption(document.body, '状态', '已完成');
    await waitFor(() =>
      expect(calls.find('GET', `${B}/runs`).some((c) => c.query.get('status') === 'completed')).toBe(true),
    );
    await pickOption(document.body, '信号', '检索了没引用');
    await waitFor(() =>
      expect(calls.find('GET', `${B}/runs`).some((c) => c.query.get('signal') === 'uncited')).toBe(true),
    );
  });

  it('链接里的「没答上来」照常筛；不认识的信号（收藏的旧链接）不发给服务端', async () => {
    const calls = api();
    const { unmount } = renderWithApp(<RunsPage />, { route: '/admin/runs?signal=unanswered' });
    expect(await screen.findByText('请假要提前几天')).toBeInTheDocument();
    expect(calls.find('GET', `${B}/runs`).every((c) => c.query.get('signal') === 'unanswered')).toBe(true);
    unmount();
    const again = api();
    renderWithApp(<RunsPage />, { route: '/admin/runs?signal=missing' });
    expect(await screen.findByText('请假要提前几天')).toBeInTheDocument();
    expect(again.find('GET', `${B}/runs`).every((c) => c.query.get('signal') === null)).toBe(true);
  });

  it('从问答大盘点过来：按带来的时间段、工具、知识库筛，显示成可以去掉的标签', async () => {
    const calls = api();
    const route =
      '/admin/runs?since=2026-10-20T00%3A00%3A00%2B08%3A00&until=2026-10-27T00%3A00%3A00%2B08%3A00' +
      '&from=2026-10-20&to=2026-10-26&tool=bash&base=builtin.kb1&baseName=制度库&status=failed';
    renderWithApp(<RunsPage />, { route, path: '/admin/runs' });
    await screen.findByText('请假要提前几天');
    const first = calls.find('GET', `${B}/runs`)[0]?.query;
    expect([
      first?.get('since'),
      first?.get('until'),
      first?.get('tool'),
      first?.get('base'),
      first?.get('status'),
    ]).toEqual(['2026-10-20T00:00:00+08:00', '2026-10-27T00:00:00+08:00', 'bash', 'builtin.kb1', 'failed']);
    expect(screen.queryByRole('group', { name: '时间段' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '去掉筛选：知识库：制度库' }));
    await waitFor(() =>
      expect(
        calls.find('GET', `${B}/runs`).some((c) => !c.query.has('base') && c.query.get('tool') === 'bash'),
      ).toBe(true),
    );
    await userEvent.click(screen.getByRole('button', { name: '去掉筛选：时间段：2026-10-20 至 2026-10-26' }));
    expect(await screen.findByRole('group', { name: '时间段' })).toBeInTheDocument();
  });
});
