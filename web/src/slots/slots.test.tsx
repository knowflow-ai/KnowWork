import { afterEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { BookOpen } from '@openai/apps-sdk-ui/components/Icon';
import { buildRoutes } from '../app/router';
import { Composer } from '../features/chat/components/Composer';
import ModelsPage from '../features/admin/models/ModelsPage';
import SettingsPage from '../features/settings/SettingsPage';
import { adminNav, ADMIN_NAV, SYSTEM_NAV, systemNav } from '../layout/nav';
import { Sidebar } from '../layout/Sidebar';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../test/render';
import { isExtensionEnabled, register, setEnabledExtensions, slotItems } from './registry';

describe('前端插槽', () => {
  afterEach(() => setEnabledExtensions(undefined));

  it('条目标了所属扩展：扩展没启用时不显示（装了、没授权的不留一个点开就加载失败的菜单）', () => {
    // 用这个文件里别的测试不看的插槽，免得留下的条目串到别的测试里
    register('insights.base.action', { sourceId: 'x', label: '开着的扩展', to: () => '/x-on' }, 'demo-on');
    register(
      'insights.base.action',
      { sourceId: 'x', label: '没授权的扩展', to: () => '/x-off' },
      'demo-off',
    );
    const labels = () => slotItems('insights.base.action').map((item) => item.label);
    expect(labels()).toEqual(expect.arrayContaining(['开着的扩展', '没授权的扩展']));
    setEnabledExtensions(['demo-on']);
    expect(labels()).toContain('开着的扩展');
    expect(labels()).not.toContain('没授权的扩展');
    expect(slotItems('insights.base.action')).toBe(slotItems('insights.base.action'));
    expect(isExtensionEnabled('demo-off')).toBe(false);
    setEnabledExtensions(undefined);
    expect(labels()).toContain('没授权的扩展');
  });

  it('没启用的扩展的页面：路由照样挂着，打开时跳回首页', async () => {
    register('app.routes', { path: 'x-off-page', element: <p>没授权的页面</p> }, 'demo-off');
    const route = slotItems('app.routes').find((r) => r.path === 'x-off-page')!;
    setEnabledExtensions([]);
    renderWithApp(<>{route.element}</>, {
      route: '/x-off-page',
      path: '/x-off-page',
      extraRoutes: { '/': <p>首页</p> },
    });
    expect(await screen.findByText('首页')).toBeInTheDocument();
    expect(screen.queryByText('没授权的页面')).not.toBeInTheDocument();
  });

  it('开源构建时插槽都是空的，菜单与路由和内置的一样', () => {
    expect(slotItems('admin.nav')).toEqual([]);
    expect(adminNav().map((group) => group.items.length)).toEqual(
      ADMIN_NAV.map((group) => group.items.length),
    );
  });

  it('扩展的菜单项插进同名分组，没有的分组新起一组', () => {
    register('admin.nav', {
      group: '能力接入',
      item: { to: '/admin/library', label: '知识库', icon: <BookOpen /> },
      before: 1,
    });
    register('admin.nav', { group: '扩展', item: { to: '/admin/x', label: '扩展页', icon: <BookOpen /> } });
    const groups = adminNav();
    const access = groups.find((group) => group.label === '能力接入')!;
    expect(access.items.map((item) => item.label)).toEqual([
      '知识源',
      '知识库',
      '技能',
      '工具与 MCP',
      '渠道',
    ]);
    expect(groups.at(-1)?.label).toBe('扩展');
    // 内置导航不被改动
    expect(ADMIN_NAV.find((group) => group.label === '能力接入')!.items).toHaveLength(4);
  });

  it('扩展的系统菜单项（例如空间管理）排在「模型」后面，内置的系统分组不被改动', () => {
    register('system.nav', { to: '/admin/workspaces', label: '空间管理', icon: <BookOpen /> });
    expect(systemNav().items.map((item) => item.label)).toEqual([
      '模型',
      '空间管理',
      '登录方式',
      '系统设置',
      '运行面板',
    ]);
    expect(SYSTEM_NAV.items).toHaveLength(4);
  });

  it('扩展的页面挂在兜底跳转之前', () => {
    register('admin.routes', { path: 'library', element: <p>知识库管理</p> });
    register('app.routes', { path: 'library', element: <p>知识库</p> });
    const routes = buildRoutes();
    const admin = routes.find((route) => route.path === '/admin')!.children!.map((r) => r.path);
    const app = routes.find((route) => route.path === '/')!.children!.map((r) => r.path);
    expect(admin.slice(-2)).toEqual(['library', '*']);
    expect(app.slice(-2)).toEqual(['library', '*']);
  });

  it('个人设置页带上扩展的标签页', async () => {
    register('settings.tabs', { key: 'library', label: '我的知识库', children: <p>知识库偏好</p> });
    renderWithApp(<SettingsPage />);
    expect(await screen.findByRole('tab', { name: '我的知识库' })).toBeInTheDocument();
  });

  it('管理后台「模型」页带上扩展的标签页，排在内置标签之后', async () => {
    routeFetch({
      'GET /api/system/model-providers': jsonResponse([]),
      'GET /api/system/models': jsonResponse([]),
      'GET /api/system/check': jsonResponse({ allowed: true }),
    });
    register('admin.models.tabs', { key: 'structure', label: '结构增强', children: <p>结构增强配置</p> });
    renderWithApp(<ModelsPage />);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['模型', '厂商', '文档解析', '结构增强']);
  });

  it('使用端侧栏在「新对话」「技能」下面放扩展的入口', async () => {
    routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/workspaces/default/conversations': jsonResponse({ items: [], next: null }),
      'GET /api/brand': jsonResponse({ name: 'KnowWork', logo_url: null }),
    });
    register('app.nav', { to: '/library', label: '知识库', icon: <BookOpen /> });
    renderWithApp(<Sidebar />);
    const nav = await screen.findByRole('navigation', { name: '主导航' });
    const links = Array.from(nav.querySelectorAll('a')).map((link) => link.textContent);
    expect(links.slice(0, 3)).toEqual(['新对话', '技能', '知识库']);
  });

  it('侧栏入口下面可以挂子项，收起时子项拿得到 collapsed；只在正好是这个地址时选中的入口，到子项的页面不跟着亮', async () => {
    routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/workspaces/default/conversations': jsonResponse({ items: [], next: null }),
      'GET /api/brand': jsonResponse({ name: 'KnowWork', logo_url: null }),
    });
    register('app.nav', {
      to: '/experts',
      end: true,
      label: '专家',
      icon: <BookOpen />,
      Sub: ({ collapsed }) =>
        collapsed ? null : (
          <a href="/experts/ex_1" className="kw-sidebar-link">
            经营分析
          </a>
        ),
    });
    renderWithApp(<Sidebar />, { route: '/experts/ex_1' });
    const nav = await screen.findByRole('navigation', { name: '主导航' });
    const links = Array.from(nav.querySelectorAll('a')).map((link) => link.textContent);
    // 注册表跨用例保留（上一条注册的知识库还在），只看子项紧跟在它的入口后面
    expect(links.slice(links.indexOf('专家'), links.indexOf('专家') + 2)).toEqual(['专家', '经营分析']);
    expect(screen.getByRole('link', { name: '专家' })).not.toHaveClass('is-active');
  });

  it('输入框左上角回答方式后面放扩展的一小段，拿得到会话 id 与回答方式；固定了回答方式的输入框不放', () => {
    routeFetch({
      'GET /api/workspaces/default/conversations/mention-options': jsonResponse({
        bases: [],
        services: [],
        skills: [],
      }),
    });
    register('chat.composer.top', {
      Component: ({ conversationId, answerMode }) => (
        <span>
          范围条 {conversationId ?? '新对话'} {answerMode}
        </span>
      ),
    });
    const send = async () => undefined;
    const { unmount } = renderWithApp(<Composer running={false} onSend={send} conversationId="c1" />);
    expect(screen.getByText('范围条 c1 agent')).toBeInTheDocument();
    unmount();
    renderWithApp(<Composer running={false} onSend={send} answerMode="analytics_model" />);
    expect(screen.queryByText(/范围条/)).toBeNull();
  });
});
