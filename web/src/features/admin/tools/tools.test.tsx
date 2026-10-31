import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { parseServer, validateServerInput, type McpServer } from '../../../api/mcp';
import { validateGrant } from '../grants/form';
import { browser } from './login';
import ToolsPage from './ToolsPage';
import { serverStatus, usableCount } from './toolsModel';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/mcp-servers';

const tool = (over: Record<string, unknown>) => ({
  name: 'mcp__github__create_issue',
  rawName: 'create_issue',
  description: '新建一个 issue',
  inputSchema: { type: 'object' },
  policy: 'ask',
  group: 'write',
  annotatedGroup: 'write',
  isNew: false,
  problem: null,
  ...over,
});

const server = (over: Record<string, unknown>) => ({
  id: 'mcp_1',
  name: 'github',
  title: 'GitHub',
  url: 'https://mcp.github.example/mcp',
  headers: [{ name: 'Authorization', last4: 'abcd' }],
  timeoutSeconds: 60,
  enabled: true,
  tools: [
    tool({
      name: 'mcp__github__search',
      rawName: 'search',
      group: 'read',
      annotatedGroup: 'read',
      policy: 'allow',
    }),
    tool({}),
    tool({ name: 'mcp__github__merge', rawName: 'merge', isNew: true }),
    tool({ name: 'mcp__github__bad', rawName: 'bad', problem: '参数结构的根不是对象' }),
  ],
  grantPattern: 'mcp__github__*',
  syncedAt: '2026-10-20T08:00:00+00:00',
  syncError: null,
  createdBy: 'u1',
  updatedAt: '2026-10-20T08:00:00+00:00',
  ...over,
});

const SERVERS = [
  server({}),
  server({
    id: 'mcp_2',
    name: 'jira',
    title: 'Jira',
    grantPattern: 'mcp__jira__*',
    syncError: '连不上 MCP 服务，请检查地址和网络',
  }),
  server({ id: 'mcp_3', name: 'wiki', title: 'Wiki', grantPattern: 'mcp__wiki__*', enabled: false }),
];

function api(extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    [`GET ${B}`]: jsonResponse(SERVERS),
    ...extra,
  });
}

const rowOf = async (title: string) => (await screen.findByText(title)).closest('tr') as HTMLElement;
const sentBody = (calls: ReturnType<typeof api>, method: string, url: string) =>
  calls.find(method, url)[0]?.body as Record<string, unknown> | undefined;

describe('工具与 MCP 页', () => {
  it('列出 MCP 服务：可用工具数、正常/连接异常/已停用三种状态', async () => {
    api();
    renderWithApp(<ToolsPage />);
    const github = await rowOf('GitHub');
    expect(within(github).getByText('3 / 4')).toBeInTheDocument();
    expect(within(github).getByText('正常')).toBeInTheDocument();
    expect(within(await rowOf('Jira')).getByText('连接异常')).toBeInTheDocument();
    expect(within(await rowOf('Wiki')).getByText('已停用')).toBeInTheDocument();
    expect(screen.getByText('共 3 条')).toBeInTheDocument();
  });

  const openAdd = async () => {
    await rowOf('GitHub');
    await userEvent.click(screen.getByRole('button', { name: /添加 MCP 服务/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText('例如：GitHub'), '工单系统');
    await userEvent.type(within(dialog).getByPlaceholderText('例如 github'), 'tickets');
    await userEvent.type(
      within(dialog).getByPlaceholderText('https://mcp.example.com/mcp'),
      'https://t.example/mcp',
    );
    return dialog;
  };

  it('添加共享凭据的服务：带上标识、地址与请求头，字段用 camelCase', async () => {
    const calls = api({ [`POST ${B}`]: jsonResponse(server({ id: 'mcp_9' }), 201) });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    await userEvent.click(within(dialog).getByRole('radio', { name: '共享凭据' }));
    await userEvent.type(within(dialog).getByLabelText('请求头的值'), 'Bearer secret-1');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(sentBody(calls, 'POST', B)).toEqual({
        name: 'tickets',
        title: '工单系统',
        url: 'https://t.example/mcp',
        timeoutSeconds: 60,
        headers: { Authorization: 'Bearer secret-1' },
        auth: 'shared',
      }),
    );
    expect(await screen.findByText('已添加，工具清单已拉取')).toBeInTheDocument();
  });

  it('添加个人访问令牌的服务（默认）：带上令牌设置与登记人自己的令牌，不发共用请求头', async () => {
    const calls = api({ [`POST ${B}`]: jsonResponse(server({ id: 'mcp_9', auth: 'user_token' }), 201) });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    expect(within(dialog).getByRole('radio', { name: '个人访问令牌' })).toBeChecked();
    expect(within(dialog).queryByLabelText('请求头的值')).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText('个人访问令牌方式，要填你自己的令牌来测试连接')).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText('给成员的说明'), '在个人设置里生成');
    await userEvent.type(within(dialog).getByLabelText('你自己的令牌'), 'tok-alice');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(sentBody(calls, 'POST', B)).toEqual({
        name: 'tickets',
        title: '工单系统',
        url: 'https://t.example/mcp',
        timeoutSeconds: 60,
        headers: {},
        auth: 'user_token',
        tokenConfig: { header: 'Authorization', prefix: 'Bearer ', help: '在个人设置里生成' },
        token: 'tok-alice',
      }),
    );
  });

  const OAUTH = {
    dynamic: true,
    clientId: 'dyn-1',
    hasSecret: true,
    scope: null,
    suggestedScope: 'tickets:read',
    redirectUri: 'http://kw.test/api/mcp/oauth/callback',
  };
  const LOGIN = '/api/workspaces/default/me/mcp-connections/mcp_9/login';

  it('填完地址探一次：对方声明了 OAuth 就默认 OAuth 授权，显示回调地址；保存后登记人跳去登录', async () => {
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined);
    const calls = api({
      [`POST ${B}/probe`]: jsonResponse({
        oauth: true,
        dynamicRegistration: true,
        scope: 'tickets:read',
        redirectUri: OAUTH.redirectUri,
      }),
      [`POST ${B}`]: jsonResponse(
        server({ id: 'mcp_9', auth: 'oauth', tools: [], syncedAt: null, oauth: OAUTH }),
        201,
      ),
      [`POST ${LOGIN}`]: jsonResponse({ authorizeUrl: 'https://as.example/authorize?state=s' }),
    });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    await userEvent.tab(); // 离开地址框时探测
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: 'OAuth 授权' })).toBeChecked());
    expect(within(dialog).getByText(OAUTH.redirectUri)).toBeInTheDocument();
    expect(within(dialog).getByText(/对方支持自动注册/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://as.example/authorize?state=s'));
    expect(sentBody(calls, 'POST', B)).toMatchObject({ auth: 'oauth', oauth: { clientId: '', scope: '' } });
    expect(sentBody(calls, 'POST', LOGIN)).toEqual({ returnTo: '/admin/tools' });
    go.mockRestore();
  });

  it('对方不要凭据（例如公开的文档检索）：默认无需认证，没有请求头', async () => {
    const calls = api({
      [`POST ${B}/probe`]: jsonResponse({ oauth: false, needsCredentials: false, redirectUri: 'x' }),
      [`POST ${B}`]: jsonResponse(server({ id: 'mcp_9', headers: [] }), 201),
    });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    await userEvent.tab();
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: '无需认证' })).toBeChecked());
    expect(within(dialog).queryByLabelText('请求头的值')).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(sentBody(calls, 'POST', B)).toMatchObject({ auth: 'shared', headers: {} }));
  });

  it('不带凭据能用、另外支持 OAuth（例如公开文档检索）：仍默认无需认证，只提示可以改成 OAuth 授权', async () => {
    api({
      [`POST ${B}/probe`]: jsonResponse({
        oauth: true,
        needsCredentials: false,
        dynamicRegistration: true,
        redirectUri: 'x',
      }),
    });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    await userEvent.tab();
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: '无需认证' })).toBeChecked());
    expect(within(dialog).getByText(/也支持 OAuth 授权/)).toBeInTheDocument();
  });

  it('管理员自己选过认证方式，探测结果不改它', async () => {
    api({
      [`POST ${B}/probe`]: jsonResponse({
        oauth: true,
        needsCredentials: true,
        dynamicRegistration: false,
        redirectUri: 'x',
      }),
    });
    renderWithApp(<ToolsPage />);
    const dialog = await openAdd();
    await userEvent.click(within(dialog).getByRole('radio', { name: '共享凭据' }));
    await userEvent.click(within(dialog).getByPlaceholderText('https://mcp.example.com/mcp'));
    await userEvent.tab();
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: '共享凭据' })).toBeChecked());
  });

  it('等待授权的服务：列表里写明，详情里登录后拉工具清单；登录回来按网址上的结果提示', async () => {
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined);
    const waiting = server({
      id: 'mcp_9',
      title: '工单系统',
      auth: 'oauth',
      tools: [],
      syncedAt: null,
      oauth: OAUTH,
    });
    const calls = api({
      [`GET ${B}`]: jsonResponse([waiting]),
      [`POST ${LOGIN}`]: jsonResponse({ authorizeUrl: 'https://as.example/authorize' }),
    });
    renderWithApp(<ToolsPage />, { route: '/admin/tools?mcpError=工单系统：你在对方页面取消了授权' });
    expect(await screen.findByText('工单系统：你在对方页面取消了授权')).toBeInTheDocument();
    const row = await rowOf('工单系统');
    expect(within(row).getByText('等待授权')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '工具' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText(/OAuth 授权（自动注册的应用）/)).toBeInTheDocument();
    await userEvent.click(within(drawer).getByRole('button', { name: '授权并拉取工具清单' }));
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://as.example/authorize'));
    expect(sentBody(calls, 'POST', LOGIN)).toEqual({ returnTo: '/admin/tools' });
    go.mockRestore();
  });

  it('修改：共享凭据改成无需认证会清掉原来的请求头；没有请求头的服务显示为无需认证', async () => {
    const calls = api({
      [`GET ${B}`]: jsonResponse([
        server({}),
        server({ id: 'mcp_2', name: 'docs', title: '文档检索', headers: [] }),
      ]),
      [`PATCH ${B}/mcp_1`]: jsonResponse(server({ headers: [] })),
    });
    renderWithApp(<ToolsPage />);
    await userEvent.click(within(await rowOf('文档检索')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '修改' }));
    const docs = await screen.findByRole('dialog');
    expect(within(docs).getByRole('radio', { name: '无需认证' })).toBeChecked();
    await userEvent.click(within(docs).getByRole('button', { name: '取消' }));
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '修改' }));
    const github = await screen.findByRole('dialog');
    expect(within(github).getByRole('radio', { name: '共享凭据' })).toBeChecked();
    await userEvent.click(within(github).getByRole('radio', { name: '无需认证' }));
    await userEvent.click(within(github).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)).toMatchObject({ headers: {} }));
  });

  it('修改个人访问令牌的服务：只改名称时不带认证设置，免得重新连接', async () => {
    const perUser = server({
      auth: 'user_token',
      headers: [],
      tokenConfig: { header: 'Authorization', prefix: 'Bearer ', help: '' },
    });
    const calls = api({
      [`GET ${B}`]: jsonResponse([perUser]),
      [`PATCH ${B}/mcp_1`]: jsonResponse(perUser),
    });
    renderWithApp(<ToolsPage />);
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '修改' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: '个人访问令牌' })).toBeChecked();
    await userEvent.type(within(dialog).getByPlaceholderText('例如：GitHub'), '（企业）');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)).toBeDefined());
    expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)).toEqual({
      title: 'GitHub（企业）',
      url: 'https://mcp.github.example/mcp',
      timeoutSeconds: 60,
    });
  });

  it('修改：不勾选「重新设置请求头」时不发请求头，标识不能改', async () => {
    const calls = api({ [`PATCH ${B}/mcp_1`]: jsonResponse(server({ title: 'GitHub 企业版' })) });
    renderWithApp(<ToolsPage />);
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '修改' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByPlaceholderText('例如 github')).toBeDisabled();
    expect(within(dialog).getByText('Authorization（末四位 abcd）')).toBeInTheDocument();
    const title = within(dialog).getByPlaceholderText('例如：GitHub');
    await userEvent.clear(title);
    await userEvent.type(title, 'GitHub 企业版');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)).toBeDefined());
    expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)).not.toHaveProperty('headers');
    expect(sentBody(calls, 'PATCH', `${B}/mcp_1`)?.title).toBe('GitHub 企业版');
  });

  it('工具清单：按只读、写入分组，每个工具一个三档；新出现的标「新」；挪组', async () => {
    const tools = `${B}/mcp_1/tools`;
    const calls = api({
      [`PATCH ${tools}/mcp__github__create_issue`]: jsonResponse(server({})),
      [`PATCH ${tools}/mcp__github__search`]: jsonResponse(server({})),
    });
    renderWithApp(<ToolsPage />);
    expect(within(await rowOf('GitHub')).getByText('新 1')).toBeInTheDocument();
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: '工具' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('不可用')).toBeInTheDocument();
    expect(within(drawer).getByText('新')).toBeInTheDocument();
    expect(
      within(drawer).getByText('有 1 个新出现的工具，已先设成需确认。', { exact: false }),
    ).toBeInTheDocument();
    const read = within(drawer).getByRole('heading', { name: '只读工具' }).closest('section') as HTMLElement;
    expect(within(read).getByText('search')).toBeInTheDocument();
    const policy = within(drawer).getByRole('radiogroup', { name: 'create_issue 怎么调用' });
    await userEvent.click(within(policy).getByRole('radio', { name: '禁用' }));
    await userEvent.click(within(read).getByRole('button', { name: '挪到写入组' }));
    await waitFor(() => expect(calls.find('PATCH', `${tools}/mcp__github__create_issue`)).toHaveLength(1));
    expect(calls.find('PATCH', `${tools}/mcp__github__create_issue`)[0]?.body).toEqual({ policy: 'block' });
    await waitFor(() =>
      expect(calls.find('PATCH', `${tools}/mcp__github__search`)[0]?.body).toEqual({ group: 'write' }),
    );
  });

  it('已收为企业 MCP 的空间服务：列表里写明，详情里提醒确认企业 MCP 能用后可以删掉这份', async () => {
    api({ [`GET ${B}`]: jsonResponse([server({ promotedTo: '企业 GitHub' })]) });
    renderWithApp(<ToolsPage />);
    const row = await rowOf('GitHub');
    expect(within(row).getByText('已收为企业 MCP')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '工具' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText(/已收为企业 MCP「企业 GitHub」/)).toBeInTheDocument();
  });

  it('给谁用：列表里写明，详情里改', async () => {
    const calls = api({
      [`GET /api/workspaces/default/grants`]: jsonResponse([
        { subject: 'role:editor', resource: 'tool:mcp__github__*', action: 'use' },
      ]),
      [`PUT /api/workspaces/default/grants/users`]: jsonResponse({
        resource: 'tool:mcp__github__*',
        subjects: [],
      }),
    });
    renderWithApp(<ToolsPage />);
    expect(within(await rowOf('GitHub')).getByText('全体成员')).toBeInTheDocument();
    expect(within(await rowOf('Jira')).getByText('只有管理员')).toBeInTheDocument();
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: '工具' }));
    const drawer = await screen.findByRole('dialog');
    await userEvent.click(within(drawer).getByRole('button', { name: '修改' }));
    const dialog = await screen.findByRole('dialog', { name: /给谁用/ });
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PUT', '/api/workspaces/default/grants/users')[0]?.body).toEqual({
        resource: 'tool:mcp__github__*',
        subjects: ['role:editor'],
      }),
    );
  });

  it('刷新与删除（删除要确认）', async () => {
    const calls = api({
      [`POST ${B}/mcp_1/sync`]: jsonResponse(server({})),
      [`DELETE ${B}/mcp_1`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<ToolsPage />);
    const row = await rowOf('GitHub');
    await userEvent.click(within(row).getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(calls.find('POST', `${B}/mcp_1/sync`)).toHaveLength(1));
    await userEvent.click(within(row).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '删除' }));
    expect(calls.find('DELETE', `${B}/mcp_1`)).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/mcp_1`)).toHaveLength(1));
  });
});

describe('MCP 显示与校验规则', () => {
  const servers = SERVERS.map((s) => parseServer(s) as McpServer);

  it('停用优先于连接异常；可用工具只算启用且参数结构合格的', () => {
    expect(servers.map((s) => serverStatus(s).text)).toEqual(['正常', '连接异常', '已停用']);
    expect(usableCount(servers[0]!)).toBe(3); // 新出现的工具是需确认，也算可用
  });

  it('添加前在本地先挡住明显的错误', () => {
    const ok = { name: 'github', title: 'GitHub', url: 'https://x.example/mcp', timeoutSeconds: 60 };
    expect(validateServerInput(ok, true)).toBeNull();
    expect(validateServerInput({ ...ok, name: 'Git_hub' }, true)).toMatch('标识');
    expect(validateServerInput({ ...ok, url: 'https://x.example/mcp?key=1' }, true)).toMatch(
      '凭据请放在请求头里',
    );
    expect(validateServerInput({ ...ok, headers: [{ name: 'Host', value: 'a' }] }, true)).toMatch('Host');
    expect(
      validateServerInput({ ...ok, headers: [{ name: 'CONTEXT7_API_KEY', value: 'k' }] }, true),
    ).toBeNull();
    expect(
      validateServerInput(
        {
          ...ok,
          headers: [
            { name: 'X-A', value: '1' },
            { name: 'x-a', value: '2' },
          ],
        },
        true,
      ),
    ).toMatch('重复');
    expect(validateServerInput({ ...ok, auth: 'user_token' }, true)).toMatch('你自己的令牌');
    expect(validateServerInput({ ...ok, auth: 'user_token' }, false)).toBeNull();
    expect(
      validateServerInput(
        { ...ok, auth: 'oauth', oauth: { clientId: '', clientSecret: 's', scope: '' } },
        true,
      ),
    ).toMatch('Client ID');
    expect(
      validateServerInput(
        { ...ok, auth: 'user_token', token: 't', tokenConfig: { header: 'Host', prefix: '', help: '' } },
        true,
      ),
    ).toMatch('Host');
  });

  it('授权表单：工具资源可以写成「前缀*」，其他类型不行', () => {
    const base = { subjectKind: 'user', subjectId: 'u1', all: false, action: 'use' } as const;
    // 工具在授权对话框里按树选，值是完整资源
    expect(validateGrant({ ...base, resourceType: 'tool', resourceId: 'tool:mcp__github__*' })).toBeNull();
    expect(validateGrant({ ...base, resourceType: 'skill', resourceId: 'report*' })).not.toBeNull();
  });
});
