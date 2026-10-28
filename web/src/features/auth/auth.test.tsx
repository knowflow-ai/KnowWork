import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch, testClient } from '../../test/render';
import LoginPage from './LoginPage';
import { safeRedirect } from './redirect';
import InvitePage from './InvitePage';
import SetupPage from './SetupPage';
import { RequireAuth } from './RequireAuth';
import { RequireAdmin } from './RequireAdmin';
import { useSessionStore } from '../../stores/session';
import { ME_KEY } from './queries';
import type { WorkspaceSummary } from '../../api/workspace';
import { pickWorkspace, useWorkspaceStore } from '../../stores/workspace';

const DEFAULT_WS: WorkspaceSummary = { id: 'default', name: '默认空间', role: 'owner' };

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().reset();
});

const initialized = {
  'GET /api/setup': jsonResponse({ initialized: true }),
  'GET /api/auth/oidc/login': jsonResponse({ detail: '未启用单点登录' }, 404),
};

async function fillLogin(email: string, password: string) {
  await userEvent.type(await screen.findByLabelText('邮箱'), email);
  await userEvent.type(screen.getByLabelText('密码'), password);
  await userEvent.click(screen.getByRole('button', { name: '登录' }));
}

describe('LoginPage', () => {
  it('登录成功后写入当前用户并回到来源页', async () => {
    routeFetch({
      ...initialized,
      'POST /api/auth/login': jsonResponse({ id: 'u', email: 'a@b.cn', name: '李' }),
    });
    const client = testClient();
    renderWithApp(<LoginPage />, {
      route: '/login',
      path: '/login',
      client,
      extraRoutes: { '/chat': <div>对话页</div> },
    });
    await fillLogin('a@b.cn', 'secret');
    expect(await screen.findByText('对话页')).toBeInTheDocument();
    expect(client.getQueryData(ME_KEY)).toMatchObject({ id: 'u', name: '李' });
    expect(window.localStorage.length).toBe(0);
    expect(screen.queryByRole('link', { name: '使用企业账号登录' })).not.toBeInTheDocument();
  });

  it('密码错误时给出明确提示；网络不通时说明原因', async () => {
    routeFetch({ ...initialized, 'POST /api/auth/login': jsonResponse({ detail: 'bad' }, 401) });
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    await fillLogin('a@b.cn', 'wrong');
    expect(await screen.findByText('邮箱或密码不正确')).toBeInTheDocument();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))),
    );
    await userEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('无法连接服务，请检查网络后重试')).toBeInTheDocument();
  });

  it('邮箱格式不对时不提交', async () => {
    const api = routeFetch(initialized);
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    await fillLogin('not-an-email', 'x');
    expect(await screen.findAllByText('邮箱格式不正确')).not.toHaveLength(0);
    expect(api.find('POST', '/api/auth/login')).toHaveLength(0);
  });

  it('配了单点登录时显示企业账号入口', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/setup'
          ? jsonResponse({ initialized: true })
          : ({ type: 'opaqueredirect', status: 0 } as Response),
      ),
    );
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    expect(await screen.findByRole('link', { name: '使用企业账号登录' })).toHaveAttribute(
      'href',
      '/api/auth/oidc/login',
    );
  });

  it('系统未初始化时转去初始化向导', async () => {
    routeFetch({
      'GET /api/setup': jsonResponse({ initialized: false }),
      'GET /api/auth/oidc/login': jsonResponse({}, 404),
    });
    renderWithApp(<LoginPage />, {
      route: '/login',
      path: '/login',
      extraRoutes: { '/setup': <div>向导</div> },
    });
    expect(await screen.findByText('向导')).toBeInTheDocument();
  });

  it('safeRedirect 只认站内路径', () => {
    expect(safeRedirect('/chat/c1')).toBe('/chat/c1');
    expect(safeRedirect('//evil.com')).toBe('/chat');
    expect(safeRedirect('https://evil.com')).toBe('/chat');
    expect(safeRedirect(undefined)).toBe('/chat');
  });
});

describe('InvitePage', () => {
  const TOKEN = 'abcdefghijklmnop_123';

  it('令牌不合法时直接说明', () => {
    renderWithApp(<InvitePage />, { route: '/invite?token=bad', path: '/invite' });
    expect(screen.getByText('邀请链接无效')).toBeInTheDocument();
  });

  it('填姓名与密码注册，成功后进入对话页', async () => {
    const api = routeFetch({
      'POST /api/auth/invitations/accept': jsonResponse({ id: 'u9', email: 'c@d.cn', name: '韩梅梅' }, 201),
    });
    const client = testClient();
    renderWithApp(<InvitePage />, {
      route: `/invite?token=${TOKEN}`,
      path: '/invite',
      client,
      extraRoutes: { '/chat': <div>对话页</div> },
    });
    await userEvent.type(screen.getByLabelText('姓名'), '韩梅梅');
    await userEvent.type(screen.getByLabelText('密码'), 'password1');
    await userEvent.type(screen.getByLabelText('确认密码'), 'password2');
    await userEvent.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(await screen.findByText('两次输入的密码不一致')).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText('确认密码'));
    await userEvent.type(screen.getByLabelText('确认密码'), 'password1');
    await userEvent.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(await screen.findByText('对话页')).toBeInTheDocument();
    expect(api.find('POST', '/api/auth/invitations/accept')[0]?.body).toEqual({
      token: TOKEN,
      name: '韩梅梅',
      password: 'password1',
    });
    expect(client.getQueryData(ME_KEY)).toMatchObject({ id: 'u9' });
  });

  it('邀请过期时说明', async () => {
    routeFetch({ 'POST /api/auth/invitations/accept': jsonResponse({ detail: '邀请无效或已过期' }, 400) });
    renderWithApp(<InvitePage />, { route: `/invite?token=${TOKEN}`, path: '/invite' });
    await userEvent.type(screen.getByLabelText('密码'), 'password1');
    await userEvent.type(screen.getByLabelText('确认密码'), 'password1');
    await userEvent.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(await screen.findByText('邀请无效或已过期')).toBeInTheDocument();
  });
});

describe('SetupPage', () => {
  it('建首个管理员后跳登录页', async () => {
    const api = routeFetch({
      'GET /api/setup': jsonResponse({ initialized: false }),
      'POST /api/setup': jsonResponse({ admin_user_id: 'u', workspace_id: 'default' }, 201),
    });
    renderWithApp(<SetupPage />, {
      route: '/setup',
      path: '/setup',
      extraRoutes: { '/login': <div>登录页</div> },
    });
    await userEvent.type(await screen.findByLabelText('姓名'), '管理员');
    await userEvent.type(screen.getByLabelText('邮箱'), 'admin@corp.cn');
    await userEvent.type(screen.getByLabelText('密码'), 'password1');
    await userEvent.type(screen.getByLabelText('确认密码'), 'password1');
    await userEvent.click(screen.getByRole('button', { name: '创建管理员' }));
    expect(await screen.findByText('登录页')).toBeInTheDocument();
    expect(api.find('POST', '/api/setup')[0]?.body).toEqual({
      email: 'admin@corp.cn',
      name: '管理员',
      password: 'password1',
    });
  });

  it('已初始化时直接去登录；提交失败时说明', async () => {
    routeFetch({ 'GET /api/setup': jsonResponse({ initialized: true }) });
    renderWithApp(<SetupPage />, {
      route: '/setup',
      path: '/setup',
      extraRoutes: { '/login': <div>登录页</div> },
    });
    expect(await screen.findByText('登录页')).toBeInTheDocument();
  });

  it('并发初始化被拒时给出后端说明', async () => {
    routeFetch({
      'GET /api/setup': jsonResponse({ initialized: false }),
      'POST /api/setup': jsonResponse({ detail: '初始化正在进行，请稍后刷新' }, 409),
    });
    renderWithApp(<SetupPage />, { route: '/setup', path: '/setup' });
    await userEvent.type(await screen.findByLabelText('姓名'), '管理员');
    await userEvent.type(screen.getByLabelText('邮箱'), 'admin@corp.cn');
    await userEvent.type(screen.getByLabelText('密码'), 'password1');
    await userEvent.type(screen.getByLabelText('确认密码'), 'password1');
    await userEvent.click(screen.getByRole('button', { name: '创建管理员' }));
    expect(await screen.findByText('初始化正在进行，请稍后刷新')).toBeInTheDocument();
  });
});

describe('RequireAuth 与 RequireAdmin', () => {
  it('未登录跳到登录页', async () => {
    routeFetch({ 'GET /api/auth/me': jsonResponse({ detail: '未登录' }, 401) });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, {
      route: '/x',
      path: '/x',
      extraRoutes: { '/login': <div>登录页</div> },
    });
    expect(await screen.findByText('登录页')).toBeInTheDocument();
  });

  it('已登录显示内容；会话中途过期跳回登录页', async () => {
    routeFetch({ 'GET /api/auth/me': jsonResponse(ME), 'GET /api/workspaces': jsonResponse([DEFAULT_WS]) });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, {
      route: '/x',
      path: '/x',
      extraRoutes: { '/login': <div>登录页</div> },
    });
    expect(await screen.findByText('受保护')).toBeInTheDocument();
    useSessionStore.getState().expire();
    expect(await screen.findByText('登录页')).toBeInTheDocument();
  });

  it('选定当前空间：上次用的还能进就用它，否则第一个', async () => {
    useWorkspaceStore.setState({ id: null });
    window.localStorage.setItem('kw.workspace', 'ws2');
    routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/workspaces': jsonResponse([DEFAULT_WS, { id: 'ws2', name: '二', role: 'editor' }]),
    });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, { route: '/x', path: '/x' });
    expect(await screen.findByText('受保护')).toBeInTheDocument();
    expect(useWorkspaceStore.getState().id).toBe('ws2');
    expect(pickWorkspace([DEFAULT_WS], 'gone')).toBe('default');
  });

  it('一个空间都进不了时说明原因，不渲染页面', async () => {
    useWorkspaceStore.setState({ id: null });
    routeFetch({ 'GET /api/auth/me': jsonResponse(ME), 'GET /api/workspaces': jsonResponse([]) });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, { route: '/x', path: '/x' });
    expect(await screen.findByText('你还不在任何工作空间里')).toBeInTheDocument();
    expect(screen.queryByText('受保护')).not.toBeInTheDocument();
  });

  it('取不到登录状态时可以重试', async () => {
    const api = routeFetch({ 'GET /api/auth/me': jsonResponse({}, 503) });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, { route: '/x', path: '/x' });
    expect(await screen.findByText('暂时无法确认登录状态')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(api.find('GET', '/api/auth/me').length).toBe(2));
  });

  it('不是管理员时看到无权限说明', async () => {
    routeFetch({ 'GET /api/workspaces/default/me/check': jsonResponse({ allowed: false }) });
    renderWithApp(<RequireAdmin>管理内容</RequireAdmin>);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
    expect(screen.queryByText('管理内容')).not.toBeInTheDocument();
  });

  it('管理员看到内容', async () => {
    routeFetch({ 'GET /api/workspaces/default/me/check': jsonResponse({ allowed: true }) });
    renderWithApp(<RequireAdmin>管理内容</RequireAdmin>);
    expect(await screen.findByText('管理内容')).toBeInTheDocument();
  });
});
