import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import AuditPage from './AuditPage';
import { validateAction } from '../../../api/audit';
import { actorTypeLabel } from './labels';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const entry = (id: string, patch: Record<string, unknown> = {}) => ({
  id,
  occurredAt: '2026-10-19T01:00:00+00:00',
  actorType: 'human',
  actorId: 'u1',
  action: 'api_key.create',
  resourceType: 'api_key',
  resourceId: 'k1',
  result: 'success',
  statusCode: 201,
  ip: '10.0.0.8',
  detail: {},
  ...patch,
});

describe('AuditPage', () => {
  it('列出记录、显示操作者姓名与结果，可加载更多与筛选', async () => {
    const calls = routeFetch({
      [`GET ${B}/members`]: jsonResponse([
        { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
      ]),
      [`GET ${B}/audit-logs`]: (call) =>
        jsonResponse(
          call.query.get('cursor')
            ? {
                items: [
                  entry('a2', { actorType: 'agent', actorId: null, result: 'denied', resourceType: null }),
                ],
                next: null,
              }
            : { items: [entry('a1'), { bad: true }], next: 'c1' },
        ),
    });
    renderWithApp(<AuditPage />);
    const row = (await screen.findByText('api_key.create')).closest('tr') as HTMLElement;
    expect(await within(row).findByText('李雷')).toBeInTheDocument();
    expect(within(row).getByText('成功')).toBeInTheDocument();
    expect(within(row).getByText('api_key：k1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(await screen.findByText('被拒绝')).toBeInTheDocument();
    expect(screen.getByText('已加载 2 条')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('动作'), { target: { value: 'API Key' } });
    expect(screen.getByText(/动作名只能是小写字母/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('动作'), { target: { value: 'api_key.*' } });
    fireEvent.keyDown(screen.getByLabelText('动作'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find('GET', `${B}/audit-logs`).at(-1)?.query.get('action')).toBe('api_key.*'),
    );
    await pickOption(document.body, '结果', '失败');
    await waitFor(() =>
      expect(calls.find('GET', `${B}/audit-logs`).at(-1)?.query.get('result')).toBe('failed'),
    );
  });

  it('无权查看时说明', async () => {
    routeFetch({
      [`GET ${B}/members`]: jsonResponse([]),
      [`GET ${B}/audit-logs`]: jsonResponse({ detail: '只有工作空间的所有者或管理员可以查看审计日志' }, 403),
    });
    renderWithApp(<AuditPage />);
    expect(await screen.findByText('审计日志加载失败，请刷新重试')).toBeInTheDocument();
  });

  it('动作名校验与标签', () => {
    expect(validateAction('')).toBeNull();
    expect(validateAction('skill.publish')).toBeNull();
    expect(validateAction('Skill')).not.toBeNull();
    expect(actorTypeLabel('agent')).toBe('Agent');
    expect(actorTypeLabel('x')).toBe('x');
  });
});
