import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import SettingsPage from './SettingsPage';
import { expiryFrom } from './expiry';

afterEach(() => vi.unstubAllGlobals());

const KEY = {
  id: 'k1',
  name: '报表脚本',
  prefix: 'ak_abcd1234',
  scopes: ['conversations:use'],
  workspaceId: 'default',
  createdAt: '2026-10-01T00:00:00+00:00',
  expiresAt: null,
  lastUsedAt: '2026-10-18T01:02:00+00:00',
  revoked: false,
};
function api(extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/auth/api-keys': jsonResponse([KEY, { ...KEY, id: 'k2', name: '旧 Key', revoked: true }]),
    'POST /api/auth/api-keys': jsonResponse(
      { ...KEY, id: 'k3', name: '新脚本', key: 'ak_new_secret_value' },
      201,
    ),
    'DELETE /api/auth/api-keys/*': new Response(null, { status: 204 }),
    'GET /api/workspaces/default/usage/me': jsonResponse([
      { key: 'chat-default', calls: 2, input_tokens: 5, output_tokens: 5, total_tokens: 10, cost: 0 },
    ]),
    ...extra,
  });
}

describe('SettingsPage', () => {
  it('账号页显示姓名与邮箱', async () => {
    api();
    renderWithApp(<SettingsPage />);
    expect(await screen.findByText('li@corp.cn')).toBeInTheDocument();
  });

  it('API Key：列表、创建后只显示一次明文、吊销需确认', async () => {
    const calls = api();
    renderWithApp(<SettingsPage />);
    await userEvent.click(screen.getByRole('tab', { name: 'API Key' }));
    const row = (await screen.findByText('报表脚本')).closest('tr') as HTMLElement;
    expect(within(row).getByText('ak_abcd1234…')).toBeInTheDocument();
    expect(within(row).getByText('使用对话')).toBeInTheDocument();
    expect(screen.getByText('已吊销')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /创建 API Key/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('名称'), { target: { value: '新脚本' } });
    await userEvent.click(within(dialog).getByRole('button', { name: '创建' }));
    expect(await screen.findByText('ak_new_secret_value')).toBeInTheDocument();
    expect(screen.getByText('请现在复制保存，关闭后无法再次查看')).toBeInTheDocument();
    const body = calls.find('POST', '/api/auth/api-keys')[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({ name: '新脚本', scopes: ['conversations:use'], workspaceId: 'default' });
    expect(typeof body.expiresAt).toBe('string');
    const issued = screen.getByRole('dialog', { name: '已创建「新脚本」' });
    await userEvent.click(within(issued).getByRole('button', { name: '我已保存' }));
    await waitFor(() => expect(screen.queryByText('ak_new_secret_value')).not.toBeInTheDocument());

    await userEvent.click(within(row).getByRole('button', { name: '吊销' }));
    const pop = await screen.findByRole('alertdialog', { name: /吊销「报表脚本」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '吊销' }));
    await waitFor(() => expect(calls.find('DELETE', '/api/auth/api-keys/k1')).toHaveLength(1));
  });

  it('个人 Key 方式去掉后不再有知识源 Key 标签', async () => {
    api();
    renderWithApp(<SettingsPage />);
    expect(await screen.findByRole('tab', { name: '我的用量' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '知识源 Key' })).not.toBeInTheDocument();
  });

  it('我的用量按模型汇总', async () => {
    const calls = api();
    renderWithApp(<SettingsPage />);
    await userEvent.click(screen.getByRole('tab', { name: '我的用量' }));
    expect(await screen.findByText('chat-default')).toBeInTheDocument();
    expect(calls.find('GET', '/api/workspaces/default/usage/me')[0]?.query.get('group_by')).toBe('model');
  });

  it('有效期换算', () => {
    const now = new Date('2026-10-19T00:00:00Z');
    expect(expiryFrom(0, now)).toBeNull();
    expect(expiryFrom(30, now)).toBe('2026-11-18T00:00:00.000Z');
  });
});
