import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../test/render';
import { AdminLayout } from './AdminLayout';
import { AppLayout } from './AppLayout';
import { flattenPages } from '../features/conversations/queries';
import { useReplyLanguage } from '../stores/replyLanguage';

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  useReplyLanguage.setState({ language: 'zh' });
});

const page1 = {
  items: [
    { id: 'c1', title: '季度总结', createdAt: null, updatedAt: null },
    { id: 'c2', title: '', createdAt: null, updatedAt: null },
  ],
  next: '2026-10-18T00:00:00+00:00',
};
const page2 = {
  items: [
    { id: 'c2', title: '', createdAt: null, updatedAt: null },
    { id: 'c3', title: '旧会话', createdAt: null, updatedAt: null },
  ],
  next: null,
};

function api(admin: boolean, extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/workspaces/default/me/check': jsonResponse({ allowed: admin }),
    'GET /api/conversations': (call) => jsonResponse(call.query.get('before') ? page2 : page1),
    'POST /api/auth/logout': new Response(null, { status: 204 }),
    ...extra,
  });
}

describe('AppLayout 与侧栏', () => {
  it('显示新对话、分页的最近会话与用户；管理入口不在侧栏，在用户菜单里', async () => {
    api(true);
    renderWithApp(<AppLayout />, {
      route: '/chat',
      path: '*',
      extraRoutes: { '/login': <div>登录页</div>, '/admin': <div>管理后台页</div> },
    });
    expect(screen.getByRole('link', { name: /新对话/ })).toHaveAttribute('href', '/chat');
    expect(await screen.findByRole('link', { name: /季度总结/ })).toHaveAttribute('href', '/chat/c1');
    expect(screen.getByRole('link', { name: /未命名对话/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('link', { name: /旧会话/ })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /未命名对话/ })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
    expect(await screen.findByText('李雷')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^成员$/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '账号菜单' }));
    await userEvent.click(await screen.findByText('管理后台'));
    expect(await screen.findByText('管理后台页')).toBeInTheDocument();
  });

  it('管理后台：分组的管理导航、返回工作台；用户菜单里不再出现「管理后台」', async () => {
    api(true);
    renderWithApp(<AdminLayout />, { route: '/admin/members', path: '*' });
    expect(await screen.findByRole('navigation', { name: '管理导航' })).toBeInTheDocument();
    for (const name of ['组织与权限', '能力接入', '运营']) expect(screen.getByText(name)).toBeInTheDocument();
    for (const name of [
      '成员',
      '部门与用户组',
      '资源授权',
      '模型',
      '知识源',
      '技能',
      '工具与 MCP',
      '用量',
      '审计',
    ]) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: /返回工作台/ })).toHaveAttribute('href', '/chat');
    await userEvent.click(screen.getByRole('button', { name: '账号菜单' }));
    expect(await screen.findByText('个人设置')).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /管理后台/ })).not.toBeInTheDocument();
  });

  it('使用端的用户菜单里选回答语言，浏览器记住所选；管理后台的菜单里没有这一项', async () => {
    api(false);
    renderWithApp(<AppLayout />, { route: '/chat', path: '*' });
    await screen.findByRole('link', { name: /季度总结/ });
    await userEvent.click(screen.getByRole('button', { name: '账号菜单' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: /回答语言/ }));
    // 子菜单靠指针移动的方向判断要不要保持展开，jsdom 没有布局，直接点选项，不模拟指针移过去
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'English' }));
    await waitFor(() => expect(useReplyLanguage.getState().language).toBe('en'));
    expect(window.localStorage.getItem('kw.replyLanguage')).toBe('en');
  });

  it('管理后台的用户菜单里没有回答语言', async () => {
    api(true);
    renderWithApp(<AdminLayout />, { route: '/admin/members', path: '*' });
    await userEvent.click(await screen.findByRole('button', { name: '账号菜单' }));
    expect(await screen.findByText('个人设置')).toBeInTheDocument();
    expect(screen.queryByText('回答语言')).not.toBeInTheDocument();
  });

  it('普通成员打开管理后台看到没有权限', async () => {
    api(false);
    renderWithApp(<AdminLayout />, { route: '/admin/members', path: '*' });
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
  });

  it('普通成员看不到管理菜单；退出登录回到登录页', async () => {
    const calls = api(false);
    renderWithApp(<AppLayout />, { route: '/chat', path: '*', extraRoutes: { '/login': <div>登录页</div> } });
    await screen.findByRole('link', { name: /季度总结/ });
    await waitFor(() => expect(calls.find('GET', '/api/workspaces/default/me/check')).toHaveLength(1));
    await userEvent.click(screen.getByRole('button', { name: '账号菜单' }));
    expect(await screen.findByText('个人设置')).toBeInTheDocument();
    expect(screen.queryByText('管理后台')).not.toBeInTheDocument();
    await userEvent.click(await screen.findByText('退出登录'));
    expect(await screen.findByText('登录页')).toBeInTheDocument();
    expect(calls.find('POST', '/api/auth/logout')).toHaveLength(1);
  });

  it('没有会话时显示提示；列表失败时说明', async () => {
    routeFetch({
      'GET /api/conversations': jsonResponse({ items: [], next: null }),
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/workspaces/default/me/check': jsonResponse({ allowed: false }),
    });
    renderWithApp(<AppLayout />);
    expect(await screen.findByText('还没有会话')).toBeInTheDocument();
  });

  it('会话可以改名与删除（删除需确认，删掉当前会话回到新对话）', async () => {
    const calls = api(false, {
      'PATCH /api/conversations/*': jsonResponse({
        id: 'c1',
        title: '年度总结',
        createdAt: null,
        updatedAt: null,
      }),
      'DELETE /api/conversations/*': new Response(null, { status: 204 }),
    });
    renderWithApp(<AppLayout />, { route: '/chat/c1', path: '*' });
    await userEvent.click(await screen.findByRole('button', { name: '更多操作：季度总结' }));
    await userEvent.click(await screen.findByText('重命名'));
    const dialog = await screen.findByRole('dialog');
    const input = within(dialog).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await within(dialog).findByText('请输入名称')).toBeInTheDocument();
    await userEvent.type(input, '年度总结');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', '/api/conversations/c1')[0]?.body).toEqual({ title: '年度总结' }),
    );

    await userEvent.click(screen.getByRole('button', { name: '更多操作：季度总结' }));
    await userEvent.click(await screen.findByText('删除'));
    const confirmDialog = await screen.findByRole('dialog', { name: '删除对话「季度总结」？' });
    await userEvent.click(within(confirmDialog).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', '/api/conversations/c1')).toHaveLength(1));
  });

  it('flattenPages 去重并容忍空值', () => {
    expect(flattenPages(undefined)).toEqual([]);
    expect(flattenPages([page1, page2]).map((c) => c.id)).toEqual(['c1', 'c2', 'c3']);
  });
});

describe('品牌与系统设置入口', () => {
  it('侧栏顶部默认显示 KnowWork；系统管理员在管理后台看得到「系统设置」', async () => {
    api(true, {
      'GET /api/system/check': jsonResponse({ allowed: true }),
      'GET /api/brand': jsonResponse({ name: 'KnowWork', logo_url: null }),
    });
    renderWithApp(<AdminLayout />, { route: '/admin/members', path: '*' });
    // 叶子 K 当首字母，整体按 KnowWork 读
    expect(await screen.findByRole('img', { name: 'KnowWork' })).toHaveTextContent('nowWork');
    expect(document.querySelector('.kw-wordmark-k')).toHaveAttribute('src', '/brand/knowwork-k.svg');
    expect(await screen.findByRole('link', { name: '系统设置' })).toHaveAttribute('href', '/admin/system');
    await waitFor(() => expect(document.title).toBe('KnowWork'));
  });

  it('改过品牌后显示自定义图标与名称，标签页标题跟着变；不是系统管理员时没有「系统设置」', async () => {
    api(false, {
      'GET /api/system/check': jsonResponse({ allowed: false }),
      'GET /api/brand': jsonResponse({ name: '星河智能', logo_url: '/api/brand/logo?v=1' }),
    });
    renderWithApp(<AppLayout />, { route: '/chat', path: '*' });
    expect(await screen.findByText('星河智能')).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe('星河智能'));
    expect(screen.queryByRole('link', { name: '系统设置' })).not.toBeInTheDocument();
  });
});
