import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import { browser } from '../admin/tools/login';
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

  it('网址里的 tab 直接打开那个标签页（对话里「管理记忆」这类入口用），切换时跟着改；不认识的打开第一个', async () => {
    api();
    const { unmount } = renderWithApp(<SettingsPage />, { route: '/settings?tab=usage' });
    expect(await screen.findByRole('tab', { name: '我的用量' })).toHaveAttribute('aria-selected', 'true');
    await userEvent.click(screen.getByRole('tab', { name: '账号' }));
    expect(screen.getByRole('tab', { name: '账号' })).toHaveAttribute('aria-selected', 'true');
    unmount();
    renderWithApp(<SettingsPage />, { route: '/settings?tab=nope' });
    expect(await screen.findByRole('tab', { name: '账号' })).toHaveAttribute('aria-selected', 'true');
  });

  it('我的用量按模型汇总', async () => {
    const calls = api();
    renderWithApp(<SettingsPage />);
    await userEvent.click(screen.getByRole('tab', { name: '我的用量' }));
    expect(await screen.findByText('chat-default')).toBeInTheDocument();
    expect(calls.find('GET', '/api/workspaces/default/usage/me')[0]?.query.get('group_by')).toBe('model');
  });

  it('连接：列出能用的全部服务与工具；不用连接的可以直接用，要连接的显示说明、发令牌；断开要确认', async () => {
    const C = '/api/workspaces/default/me/mcp-connections';
    const calls = api({
      [`GET ${C}`]: jsonResponse([
        {
          serverId: 'mcp_0',
          title: 'Context7',
          auth: 'shared',
          status: 'ready',
          tools: [
            { name: 'resolve-library-id', description: '按名字找到库' },
            { name: 'query-docs', description: '查这个库的文档' },
          ],
        },
        {
          serverId: 'mcp_1',
          title: 'GitLab',
          auth: 'user_token',
          help: '在偏好设置 → 访问令牌里生成',
          status: 'none',
          tools: [{ name: 'list_merge_requests', description: '列出合并请求' }],
        },
        {
          serverId: 'mcp_2',
          title: 'Jira',
          auth: 'user_token',
          help: '',
          status: 'invalid',
          display: '****9876',
          connectedAt: '2026-10-20T08:00:00+00:00',
        },
      ]),
      [`PUT ${C}/mcp_1`]: jsonResponse({ serverId: 'mcp_1', title: 'GitLab', status: 'connected' }),
      [`DELETE ${C}/mcp_2`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<SettingsPage />, { route: '/settings?tab=connections' });
    const context7 = (await screen.findByText('Context7')).closest('tr') as HTMLElement;
    expect(within(context7).getByText('可以直接用')).toBeInTheDocument();
    expect(within(context7).getByText('resolve-library-id、query-docs（2 个）')).toBeInTheDocument();
    expect(within(context7).queryByRole('button', { name: '连接' })).not.toBeInTheDocument();
    await userEvent.click(within(context7).getByRole('button', { name: '查看' }));
    const tools = await screen.findByRole('dialog', { name: '「Context7」的工具' });
    expect(within(tools).getByText('查这个库的文档')).toBeInTheDocument();
    await userEvent.click(within(tools).getByRole('button', { name: '知道了' }));
    const gitlab = screen.getByText('GitLab').closest('tr') as HTMLElement;
    expect(within(gitlab).getByText('未连接')).toBeInTheDocument();
    const jira = screen.getByText('Jira').closest('tr') as HTMLElement;
    expect(within(jira).getByText('已失效，请重新连接')).toBeInTheDocument();

    await userEvent.click(within(gitlab).getByRole('button', { name: '连接' }));
    const dialog = await screen.findByRole('dialog', { name: '连接「GitLab」' });
    expect(within(dialog).getByText('在偏好设置 → 访问令牌里生成')).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText('你的令牌'), ' tok-1 ');
    await userEvent.click(within(dialog).getByRole('button', { name: '连接' }));
    await waitFor(() => expect(calls.find('PUT', `${C}/mcp_1`)[0]?.body).toEqual({ token: 'tok-1' }));
    expect(await screen.findByText('已连接，新开一个对话就能用「GitLab」')).toBeInTheDocument();

    await userEvent.click(within(jira).getByRole('button', { name: '断开' }));
    const pop = await screen.findByRole('alertdialog', { name: /断开「Jira」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '断开' }));
    await waitFor(() => expect(calls.find('DELETE', `${C}/mcp_2`)).toHaveLength(1));
  });

  it('连接：OAuth 授权的服务点「连接」跳到对方页面；回来时提示结果并去掉网址上的参数', async () => {
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined);
    const C = '/api/workspaces/default/me/mcp-connections';
    const calls = api({
      [`GET ${C}`]: jsonResponse([
        { serverId: 'mcp_1', title: 'GitHub', auth: 'oauth', status: 'connected', connectedAt: null },
        { serverId: 'mcp_2', title: 'Notion', auth: 'oauth', status: 'none' },
      ]),
      [`POST ${C}/mcp_2/login`]: jsonResponse({ authorizeUrl: 'https://notion.example/authorize' }),
    });
    renderWithApp(<SettingsPage />, { route: '/settings?tab=connections&mcpConnected=mcp_1' });
    expect(await screen.findByText('已连接，新开一个对话就能用')).toBeInTheDocument();
    const github = (await screen.findByText('GitHub')).closest('tr') as HTMLElement;
    expect(within(github).getByText('已连接 · OAuth 授权')).toBeInTheDocument();
    const notion = screen.getByText('Notion').closest('tr') as HTMLElement;
    await userEvent.click(within(notion).getByRole('button', { name: '连接' }));
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://notion.example/authorize'));
    expect(calls.find('POST', `${C}/mcp_2/login`)[0]?.body).toEqual({
      returnTo: '/settings?tab=connections',
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    go.mockRestore();
  });

  it('有效期换算', () => {
    const now = new Date('2026-10-19T00:00:00Z');
    expect(expiryFrom(0, now)).toBeNull();
    expect(expiryFrom(30, now)).toBe('2026-11-18T00:00:00.000Z');
  });
});
