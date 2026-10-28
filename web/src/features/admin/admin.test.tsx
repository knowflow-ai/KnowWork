import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import MembersPage from './members/MembersPage';
import { includesText } from './table';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const MEMBERS = [
  { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
  { user_id: 'u2', role: 'editor', name: '韩梅梅', email: 'han@corp.cn' },
];

describe('MembersPage', () => {
  function members(extra = {}) {
    return routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      [`GET ${B}/members`]: jsonResponse(MEMBERS),
      ...extra,
    });
  }

  it('列出姓名、邮箱与角色，可搜索', async () => {
    members();
    renderWithApp(<MembersPage />);
    expect(await screen.findByText('韩梅梅')).toBeInTheDocument();
    expect(screen.getByText('han@corp.cn')).toBeInTheDocument();
    expect(screen.getByText('所有者')).toBeInTheDocument();
    expect(screen.getByText('共 2 条')).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('搜索姓名、邮箱或角色'), '不存在');
    expect(await screen.findByText('没有找到匹配的成员')).toBeInTheDocument();
  });

  it('改角色、移出（需确认，不能移出自己）', async () => {
    const api = members({
      [`PATCH ${B}/members/*`]: jsonResponse({}),
      [`DELETE ${B}/members/*`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<MembersPage />);
    const row = (await screen.findByText('韩梅梅')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '改角色' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(within(dialog).getByRole('group', { name: '角色' })).getByRole('button'));
    await userEvent.click(await screen.findByRole('option', { name: '管理员' }));
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.find('PATCH', `${B}/members/u2`)[0]?.body).toEqual({ role: 'admin' }));

    const self = screen.getByText('李雷').closest('tr') as HTMLElement;
    expect(within(self).getByRole('button', { name: '移出' })).toBeDisabled();
    await userEvent.click(within(row).getByRole('button', { name: '移出' }));
    const pop = await screen.findByRole('alertdialog', { name: /把「韩梅梅」移出空间/ });
    await userEvent.click(within(pop).getByRole('button', { name: '移出' }));
    await waitFor(() => expect(api.find('DELETE', `${B}/members/u2`)).toHaveLength(1));
  });

  it('添加成员：搜索账号，已是成员的不出现', async () => {
    const api = members({
      'GET /api/workspaces/default/users': jsonResponse([
        { id: 'u2', email: 'han@corp.cn', name: '韩梅梅' },
        { id: 'u3', email: 'wei@corp.cn', name: '魏华' },
      ]),
      [`POST ${B}/members`]: jsonResponse({}, 201),
    });
    renderWithApp(<MembersPage />);
    await screen.findByText('韩梅梅');
    await userEvent.click(screen.getByRole('button', { name: /添加成员/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '添加' }));
    expect(await screen.findByText('请选择要添加的用户')).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole('combobox', { name: '选择用户' }), 'corp');
    await userEvent.click(await screen.findByRole('option', { name: '魏华（wei@corp.cn）' }));
    expect(screen.queryByText('韩梅梅（han@corp.cn）')).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '添加' }));
    await waitFor(() =>
      expect(api.find('POST', `${B}/members`)[0]?.body).toEqual({ user_id: 'u3', role: 'editor' }),
    );
  });

  it('邀请新用户：生成的链接只显示这一次', async () => {
    const api = members({
      'POST /api/workspaces/default/invitations': jsonResponse(
        { id: 'i1', email: 'new@corp.cn', url: 'https://kw/invite?token=abc', expiresAt: 'x' },
        201,
      ),
    });
    renderWithApp(<MembersPage />);
    await screen.findByText('韩梅梅');
    await userEvent.click(screen.getByRole('button', { name: '邀请新用户' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText('name@company.com'), 'new@corp.cn');
    await userEvent.click(within(dialog).getByRole('button', { name: '生成邀请链接' }));
    expect(await screen.findByText('https://kw/invite?token=abc')).toBeInTheDocument();
    expect(api.find('POST', '/api/workspaces/default/invitations')[0]?.body).toEqual({
      email: 'new@corp.cn',
      role: 'editor',
    });
    await userEvent.click(within(dialog).getByRole('button', { name: '完成' }));
  });

  it('列表加载失败时说明', async () => {
    routeFetch({ 'GET /api/auth/me': jsonResponse(ME), [`GET ${B}/members`]: jsonResponse({}, 403) });
    renderWithApp(<MembersPage />);
    expect(await screen.findByText('成员列表加载失败，请刷新重试')).toBeInTheDocument();
  });
});

describe('includesText', () => {
  it('忽略大小写与空关键词', () => {
    expect(includesText(['Alice', null], ' ali ')).toBe(true);
    expect(includesText([undefined], '')).toBe(true);
    expect(includesText(['Bob'], 'x')).toBe(false);
  });
});
