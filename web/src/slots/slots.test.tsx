import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { BookOpen } from '@openai/apps-sdk-ui/components/Icon';
import { buildRoutes } from '../app/router';
import ModelsPage from '../features/admin/models/ModelsPage';
import SettingsPage from '../features/settings/SettingsPage';
import { adminNav, ADMIN_NAV, SYSTEM_NAV, systemNav } from '../layout/nav';
import { Sidebar } from '../layout/Sidebar';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../test/render';
import { register, slotItems } from './registry';

describe('前端插槽', () => {
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
    expect(systemNav().items.map((item) => item.label)).toEqual(['模型', '空间管理', '系统设置', '运行面板']);
    expect(SYSTEM_NAV.items).toHaveLength(3);
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

  it('使用端侧栏在「新对话」下面放扩展的入口', async () => {
    routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/workspaces/default/conversations': jsonResponse({ items: [], next: null }),
      'GET /api/brand': jsonResponse({ name: 'KnowWork', logo_url: null }),
    });
    register('app.nav', { to: '/library', label: '知识库', icon: <BookOpen /> });
    renderWithApp(<Sidebar />);
    const nav = await screen.findByRole('navigation', { name: '主导航' });
    const links = Array.from(nav.querySelectorAll('a')).map((link) => link.textContent);
    expect(links.slice(0, 2)).toEqual(['新对话', '知识库']);
  });
});
