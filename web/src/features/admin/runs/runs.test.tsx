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
};

function api() {
  return routeFetch({
    [`GET ${B}/runs`]: jsonResponse({ items: [ROW], next: null }),
    [`GET ${B}/members`]: jsonResponse([
      { user_id: 'u1', role: 'viewer', name: '李雷', email: 'li@corp.cn' },
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
  it('列出每一轮：成员、会话、状态、耗时与用量；点开看运行详情（走管理端接口）', async () => {
    const calls = api();
    renderWithApp(<RunsPage />);
    expect(await screen.findByText('请假条')).toBeInTheDocument();
    expect(screen.getByText('第 2 轮')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('4.2 秒')).toBeInTheDocument();
    expect(calls.find('GET', `${B}/runs`)[0]?.query.get('since')).toBeTruthy();
    await userEvent.click(screen.getByText('请假条'));
    expect(await screen.findByText(/请假条 · 第 2 轮/)).toBeInTheDocument();
    await waitFor(() => expect(calls.find('GET', `${B}/runs/c1/turns/2`)).toHaveLength(1));
  });

  it('按状态筛选时带上参数', async () => {
    const calls = api();
    renderWithApp(<RunsPage />);
    await screen.findByText('请假条');
    await pickOption(document.body, '状态', '已完成');
    await waitFor(() =>
      expect(calls.find('GET', `${B}/runs`).some((c) => c.query.get('status') === 'completed')).toBe(true),
    );
  });
});
