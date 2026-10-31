/** 「工具与 MCP」的「企业」页签：装了扩展才有；空间管理员看到发到本空间的企业 MCP，可以停用、改给谁用；系统管理员看扩展给的内容。 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { register } from '../../../slots/registry';
import ToolsPage from './ToolsPage';

afterEach(() => vi.unstubAllGlobals());

const W = '/api/workspaces/default';

const here = (over: Record<string, unknown>) => ({
  serverId: 'mcp_e1',
  title: '企业 GitLab',
  url: 'https://gitlab.corp/mcp',
  auth: 'oauth',
  tools: 5,
  off: false,
  narrowed: false,
  resource: 'tool:mcp__ent_gitlab__*',
  ...over,
});

function api(extra = {}, systemAdmin = false) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/system/check': jsonResponse({ allowed: systemAdmin }),
    [`GET ${W}/mcp-servers`]: jsonResponse([]),
    [`GET ${W}/grants`]: jsonResponse([
      { subject: 'user:u1', resource: 'tool:mcp__ent_wiki__*', action: 'use' },
    ]),
    [`GET ${W}/enterprise-mcp`]: jsonResponse({
      servers: [
        here({}),
        here({
          serverId: 'mcp_e2',
          title: '企业 Wiki',
          auth: 'shared',
          narrowed: true,
          resource: 'tool:mcp__ent_wiki__*',
        }),
      ],
    }),
    [`GET ${W}/members`]: jsonResponse([{ user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' }]),
    [`GET ${W}/departments`]: jsonResponse([]),
    [`GET ${W}/groups`]: jsonResponse([]),
    ...extra,
  });
}

const rowOf = async (title: string) => (await screen.findByText(title)).closest('tr') as HTMLElement;

describe('工具与 MCP → 企业', () => {
  beforeAll(() => register('admin.tools.enterprise', { children: <p>系统管理员的企业 MCP 管理</p> }));

  it('空间管理员：列出发到本空间的企业 MCP，写明给谁用；可以在本空间停用', async () => {
    const calls = api({ [`POST ${W}/enterprise-mcp/disable`]: new Response(null, { status: 204 }) });
    renderWithApp(<ToolsPage />, { route: '/admin/tools?tab=enterprise' });
    expect(screen.getByRole('tab', { name: '空间 MCP' })).toBeInTheDocument();
    const gitlab = await rowOf('企业 GitLab');
    expect(within(gitlab).getByText('全体成员')).toBeInTheDocument();
    expect(within(gitlab).getByText('OAuth 授权')).toBeInTheDocument();
    expect(within(await rowOf('企业 Wiki')).getByText('1 个对象')).toBeInTheDocument();
    expect(screen.queryByText('系统管理员的企业 MCP 管理')).toBeNull();
    await userEvent.click(within(gitlab).getByRole('button', { name: '在本空间停用' }));
    await waitFor(() =>
      expect(calls.find('POST', `${W}/enterprise-mcp/disable`)[0]?.body).toEqual({ serverId: 'mcp_e1' }),
    );
  });

  it('改给谁用时带上现在选的对象', async () => {
    const calls = api({
      [`PUT ${W}/enterprise-mcp/users`]: jsonResponse({ serverId: 'mcp_e2', narrowed: true }),
    });
    renderWithApp(<ToolsPage />, { route: '/admin/tools?tab=enterprise' });
    await userEvent.click(within(await rowOf('企业 Wiki')).getByRole('button', { name: '给谁用' }));
    const dialog = await screen.findByRole('dialog', { name: /在本空间给谁用/ });
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PUT', `${W}/enterprise-mcp/users`)[0]?.body).toEqual({
        serverId: 'mcp_e2',
        subjects: ['user:u1'],
      }),
    );
  });

  it('系统管理员看到扩展给的管理视图', async () => {
    api({}, true);
    renderWithApp(<ToolsPage />, { route: '/admin/tools?tab=enterprise' });
    expect(await screen.findByText('系统管理员的企业 MCP 管理')).toBeInTheDocument();
  });
});
