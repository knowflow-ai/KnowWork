import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../test/render';
import UsagePage from '../admin/UsagePage';
import { isoDate, rangeOf } from './range';
import { totals } from './range';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const ROWS = [
  { key: 'u1', calls: 3, input_tokens: 1000, output_tokens: 200, total_tokens: 1200, cost: '0.5' },
  { key: 'u9', calls: 1, input_tokens: 10, output_tokens: 5, total_tokens: 15, cost: 0 },
];

describe('用量', () => {
  it('按成员汇总，显示姓名与合计；切换汇总方式与时间范围重新查询', async () => {
    const calls = routeFetch({
      [`GET ${B}/usage`]: jsonResponse(ROWS),
      [`GET ${B}/members`]: jsonResponse([
        { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
      ]),
    });
    renderWithApp(<UsagePage />);
    expect(await screen.findByText('李雷')).toBeInTheDocument();
    expect(screen.getByText('u9')).toBeInTheDocument();
    const summary = screen.getByText('合计').closest('tr') as HTMLElement;
    expect(within(summary).getByText('1,215')).toBeInTheDocument();
    expect(within(summary).getByText('0.50')).toBeInTheDocument();
    expect(calls.find('GET', `${B}/usage`)[0]?.query.get('group_by')).toBe('user');
    await userEvent.click(screen.getByText('按模型'));
    await waitFor(() => expect(calls.find('GET', `${B}/usage`).at(-1)?.query.get('group_by')).toBe('model'));
    await userEvent.click(screen.getByText('最近 30 天'));
    await waitFor(() =>
      expect(calls.find('GET', `${B}/usage`).at(-1)?.query.get('start')).toBe(rangeOf('30d').start),
    );
  });

  it('没有用量与加载失败时说明', async () => {
    routeFetch({ [`GET ${B}/usage`]: jsonResponse([]), [`GET ${B}/members`]: jsonResponse([]) });
    renderWithApp(<UsagePage />);
    expect(await screen.findByText('这段时间没有用量')).toBeInTheDocument();
  });

  it('时间范围按本机日历计算', () => {
    const today = new Date(2026, 9, 19);
    expect(isoDate(today)).toBe('2026-10-19');
    expect(rangeOf('7d', today)).toEqual({ start: '2026-10-13', end: '2026-10-19' });
    expect(rangeOf('30d', today)).toEqual({ start: '2026-09-20', end: '2026-10-19' });
    expect(rangeOf('month', today)).toEqual({ start: '2026-10-01', end: '2026-10-19' });
    expect(totals([])).toMatchObject({ calls: 0, cost: 0 });
  });
});
