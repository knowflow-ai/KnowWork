import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { parseServer, validateServerInput, type McpServer } from '../../../api/mcp';
import { validateGrant } from '../grants/form';
import ToolsPage from './ToolsPage';
import { serverStatus, usableCount } from './toolsModel';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/mcp-servers';

const tool = (over: Record<string, unknown>) => ({
  name: 'mcp__github__create_issue',
  rawName: 'create_issue',
  description: '新建一个 issue',
  inputSchema: { type: 'object' },
  risk: 'write',
  riskOverridden: false,
  approval: 'ask',
  approvalOverridden: false,
  enabled: true,
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
    tool({ name: 'mcp__github__search', rawName: 'search', risk: 'read', approval: 'never' }),
    tool({}),
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
  server({ id: 'mcp_2', name: 'jira', title: 'Jira', syncError: '连不上 MCP 服务，请检查地址和网络' }),
  server({ id: 'mcp_3', name: 'wiki', title: 'Wiki', enabled: false }),
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
    expect(within(github).getByText('2 / 3')).toBeInTheDocument();
    expect(within(github).getByText('正常')).toBeInTheDocument();
    expect(within(await rowOf('Jira')).getByText('连接异常')).toBeInTheDocument();
    expect(within(await rowOf('Wiki')).getByText('已停用')).toBeInTheDocument();
    expect(screen.getByText('共 3 条')).toBeInTheDocument();
  });

  it('添加：带上标识、地址与请求头，字段用 camelCase', async () => {
    const calls = api({ [`POST ${B}`]: jsonResponse(server({ id: 'mcp_9' }), 201) });
    renderWithApp(<ToolsPage />);
    await rowOf('GitHub');
    await userEvent.click(screen.getByRole('button', { name: /添加 MCP 服务/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText('例如：GitHub'), '工单系统');
    await userEvent.type(within(dialog).getByPlaceholderText('例如 github'), 'tickets');
    await userEvent.type(
      within(dialog).getByPlaceholderText('https://mcp.example.com/mcp'),
      'https://t.example/mcp',
    );
    await userEvent.type(within(dialog).getByLabelText('请求头的值'), 'Bearer secret-1');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(sentBody(calls, 'POST', B)).toEqual({
        name: 'tickets',
        title: '工单系统',
        url: 'https://t.example/mcp',
        timeoutSeconds: 60,
        headers: { Authorization: 'Bearer secret-1' },
      }),
    );
    expect(await screen.findByText('已添加，工具清单已拉取')).toBeInTheDocument();
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

  it('工具清单：改风险等级、停用某个工具，各发一次设置', async () => {
    const tools = `${B}/mcp_1/tools`;
    const calls = api({
      [`PATCH ${tools}/mcp__github__create_issue`]: jsonResponse(server({})),
    });
    renderWithApp(<ToolsPage />);
    await userEvent.click(within(await rowOf('GitHub')).getByRole('button', { name: '工具' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('不可用')).toBeInTheDocument();
    await pickOption(drawer, 'create_issue 的风险等级', '高风险');
    await userEvent.click(within(drawer).getByLabelText('启用 create_issue'));
    await waitFor(() => expect(calls.find('PATCH', `${tools}/mcp__github__create_issue`)).toHaveLength(2));
    expect(calls.find('PATCH', `${tools}/mcp__github__create_issue`).map((c) => c.body)).toEqual([
      { risk: 'dangerous' },
      { enabled: false },
    ]);
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
    expect(usableCount(servers[0]!)).toBe(2);
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
  });

  it('授权表单：工具资源可以写成「前缀*」，其他类型不行', () => {
    const base = { subjectKind: 'user', subjectId: 'u1', all: false, action: 'use' } as const;
    expect(validateGrant({ ...base, resourceType: 'tool', resourceId: 'mcp__github__*' })).toBeNull();
    expect(validateGrant({ ...base, resourceType: 'skill', resourceId: 'report*' })).not.toBeNull();
  });
});
