import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch, testClient } from '../../test/render';
import LoginPage from './LoginPage';
import { inClientTarget, navigation } from './inClient';
import { safeRedirect } from './redirect';
import InvitePage from './InvitePage';
import SetupPage from './SetupPage';
import { RequireAuth } from './RequireAuth';
import { RequireAdmin } from './RequireAdmin';
import { useSessionStore } from '../../stores/session';
import { ME_KEY, rememberUser } from './queries';
import { isExtensionEnabled, setEnabledExtensions } from '../../slots/registry';
import type { LoginMethod } from '../../api/auth';
import type { WorkspaceSummary } from '../../api/workspace';
import { pickWorkspace, useWorkspaceStore } from '../../stores/workspace';

const DEFAULT_WS: WorkspaceSummary = { id: 'default', name: '默认空间', role: 'owner' };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  useSessionStore.getState().reset();
});

const NO_METHODS = { password: true, methods: [] };
const initialized = {
  'GET /api/setup': jsonResponse({ initialized: true }),
  'GET /api/auth/methods': jsonResponse(NO_METHODS),
};
const OIDC_METHOD = {
  id: 'oidc',
  kind: 'oidc',
  label: '企业账号',
  style: 'redirect',
  url: '/api/auth/oidc/login',
  inClientAgents: [],
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

  it('按服务端给的登录方式显示入口', async () => {
    const wecom = {
      ...OIDC_METHOD,
      id: 'wx',
      kind: 'wecom',
      label: '企业微信',
      url: '/api/auth/sso/wx/login',
    };
    const ad = {
      ...OIDC_METHOD,
      id: 'ad',
      kind: 'ldap',
      label: '域账号',
      style: 'password',
      url: '/api/auth/password/ad',
    };
    routeFetch({
      ...initialized,
      'GET /api/auth/methods': jsonResponse({ password: true, methods: [OIDC_METHOD, wecom, ad] }),
    });
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    expect(await screen.findByRole('link', { name: '使用企业账号登录' })).toHaveAttribute(
      'href',
      '/api/auth/oidc/login',
    );
    expect(screen.getByRole('link', { name: '使用企业微信登录' })).toHaveAttribute(
      'href',
      '/api/auth/sso/wx/login',
    );
    // 账号密码类（域账号）的入口随域账号登录一起做，现在不显示成跳转链接
    expect(screen.queryByRole('link', { name: '使用域账号登录' })).not.toBeInTheDocument();
  });

  it('点单点登录前记下登录后要回的页面', async () => {
    routeFetch({
      ...initialized,
      'GET /api/auth/methods': jsonResponse({ password: true, methods: [OIDC_METHOD] }),
    });
    renderWithApp(<LoginPage />, {
      route: '/login',
      path: '/login',
      state: { from: '/oauth/authorize?client_id=kw-desktop' },
    });
    const link = await screen.findByRole('link', { name: '使用企业账号登录' });
    link.addEventListener('click', (event) => event.preventDefault());
    await userEvent.click(link);
    expect(window.sessionStorage.getItem('kw-after-login')).toBe('/oauth/authorize?client_id=kw-desktop');
  });

  it('在企业微信里打开：直接走企业微信的免登', async () => {
    const wecom = {
      ...OIDC_METHOD,
      id: 'ds_wx',
      kind: 'wecom',
      label: '企业微信',
      url: '/api/auth/sso/ds_wx/login',
      inClientAgents: ['wxwork'],
    };
    routeFetch({
      ...initialized,
      'GET /api/auth/methods': jsonResponse({ password: true, methods: [OIDC_METHOD, wecom] }),
    });
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 wxwork/4.1.30 MicroMessenger/7.0.1');
    const go = vi.spyOn(navigation, 'go').mockImplementation(() => undefined);
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    await waitFor(() => expect(go).toHaveBeenCalledWith('/api/auth/sso/ds_wx/login?in_client=1'));
  });

  it('自动免登一分钟内只跳一次，?local=1 不跳', () => {
    const oidc: LoginMethod = { ...OIDC_METHOD, style: 'redirect' };
    const wecom: LoginMethod = {
      ...oidc,
      id: 'ds_wx',
      kind: 'wecom',
      label: '企业微信',
      url: '/api/auth/sso/ds_wx/login',
      inClientAgents: ['wxwork'],
    };
    const ua = 'Mozilla/5.0 wxwork/4.1.30';
    expect(inClientTarget([oidc, wecom], 'Mozilla/5.0 Chrome/130', '', 0)).toBeNull();
    expect(inClientTarget([wecom], ua, '?local=1', 0)).toBeNull();
    expect(inClientTarget([wecom], ua, '', 1_000)).toBe('/api/auth/sso/ds_wx/login?in_client=1');
    expect(inClientTarget([wecom], ua, '', 30_000)).toBeNull();
    expect(inClientTarget([wecom], ua, '', 70_000)).not.toBeNull();
  });

  it('开了域账号登录：切到域账号，用账号密码登录', async () => {
    const ad = {
      ...OIDC_METHOD,
      id: 'ad',
      kind: 'ldap',
      label: '集团域账号',
      style: 'password',
      url: '/api/auth/password/ad',
    };
    const api = routeFetch({
      ...initialized,
      'GET /api/auth/methods': jsonResponse({ password: true, methods: [ad] }),
      'POST /api/auth/password/ad': jsonResponse({ id: 'u', email: null, name: '张三' }),
    });
    const client = testClient();
    renderWithApp(<LoginPage />, {
      route: '/login',
      path: '/login',
      client,
      extraRoutes: { '/chat': <div>对话页</div> },
    });
    await userEvent.click(await screen.findByRole('radio', { name: '集团域账号' }));
    expect(screen.getByText('使用集团域账号登录')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('账号'), 'zhangsan');
    await userEvent.type(screen.getByLabelText('密码'), 'Corp-Pass-1');
    await userEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('对话页')).toBeInTheDocument();
    expect(api.find('POST', '/api/auth/password/ad')[0]?.body).toEqual({
      username: 'zhangsan',
      password: 'Corp-Pass-1',
    });
    expect(client.getQueryData(ME_KEY)).toMatchObject({ id: 'u', email: null });
  });

  it('域账号密码不对时说清楚', async () => {
    const ad = {
      ...OIDC_METHOD,
      id: 'ad',
      kind: 'ldap',
      label: '域账号',
      style: 'password',
      url: '/api/auth/password/ad',
    };
    routeFetch({
      ...initialized,
      'GET /api/auth/methods': jsonResponse({ password: true, methods: [ad] }),
      'POST /api/auth/password/ad': jsonResponse({ detail: '账号或密码错误' }, 401),
    });
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    await userEvent.click(await screen.findByRole('radio', { name: '域账号' }));
    await userEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findAllByText('请输入账号')).not.toHaveLength(0);
    await userEvent.type(screen.getByLabelText('账号'), 'zhangsan');
    await userEvent.type(screen.getByLabelText('密码'), 'nope');
    await userEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('账号或密码不正确')).toBeInTheDocument();
  });

  it('取不到登录方式时照常能用账号密码登录', async () => {
    routeFetch({ ...initialized, 'GET /api/auth/methods': jsonResponse({ detail: 'x' }, 500) });
    renderWithApp(<LoginPage />, { route: '/login', path: '/login' });
    expect(await screen.findByLabelText('邮箱')).toBeInTheDocument();
    expect(screen.queryByText('或')).not.toBeInTheDocument();
  });

  it('系统未初始化时转去初始化向导', async () => {
    routeFetch({
      'GET /api/setup': jsonResponse({ initialized: false }),
      'GET /api/auth/methods': jsonResponse(NO_METHODS),
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
  it('单点登录回来后回到登录前要去的页面（例如桌面端的登录确认页）', async () => {
    window.sessionStorage.setItem('kw-after-login', '/oauth/authorize?client_id=kw-desktop');
    routeFetch({
      'GET /api/auth/me': jsonResponse({ ...ME, extensions: [] }),
      'GET /api/workspaces': jsonResponse([DEFAULT_WS]),
    });
    renderWithApp(<RequireAuth>主界面</RequireAuth>, {
      route: '/x',
      path: '/x',
      extraRoutes: { '/oauth/authorize': <div>确认页</div> },
    });
    expect(await screen.findByText('确认页')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('kw-after-login')).toBeNull();
    setEnabledExtensions(undefined);
  });

  it('当前用户信息带着启用了的扩展：只显示这些扩展的入口', async () => {
    routeFetch({
      'GET /api/auth/me': jsonResponse({ ...ME, extensions: ['demo-on'] }),
      'GET /api/workspaces': jsonResponse([DEFAULT_WS]),
    });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, { route: '/x', path: '/x' });
    expect(await screen.findByText('受保护')).toBeInTheDocument();
    expect(isExtensionEnabled('demo-on')).toBe(true);
    expect(isExtensionEnabled('demo-off')).toBe(false);
    setEnabledExtensions(undefined);
  });

  it('登录后写进缓存的用户信息还没有扩展列表：重新取到之前不进主界面，免得菜单先多后少', async () => {
    const client = testClient();
    let release: () => void = () => undefined;
    const fetched = new Promise<void>((resolve) => {
      release = resolve;
    });
    routeFetch({
      'GET /api/auth/me': async () => {
        await fetched;
        return jsonResponse({ ...ME, extensions: [] });
      },
      'GET /api/workspaces': jsonResponse([DEFAULT_WS]),
    });
    rememberUser(client, { id: ME.id, email: ME.email, name: ME.name });
    renderWithApp(<RequireAuth>受保护</RequireAuth>, { route: '/x', path: '/x', client });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('受保护')).not.toBeInTheDocument();
    release();
    expect(await screen.findByText('受保护')).toBeInTheDocument();
    setEnabledExtensions(undefined);
  });

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
