import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { renderWithApp } from '../../test/render';
import { RowActions } from './common';
import { buildRoutes } from '../../app/router';

describe('RowActions', () => {
  it('超过 3 个操作时收进「更多」，收进去的删除用对话框确认', async () => {
    const remove = vi.fn();
    const copy = vi.fn();
    renderWithApp(
      <RowActions
        actions={[
          { key: 'a', label: '修改', onClick: vi.fn() },
          { key: 'b', label: '测试', onClick: vi.fn() },
          { key: 'c', label: '复制', onClick: copy },
          { key: 'd', label: '删除', danger: true, confirm: '确定删除？', onClick: remove },
        ]}
      />,
    );
    expect(screen.getByRole('button', { name: '修改' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '复制' }));
    expect(copy).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '删除' }));
    const dialog = await screen.findByRole('dialog', { name: '确定删除？' });
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(remove).toHaveBeenCalled());
  });
});

describe('路由', () => {
  it('路由表：使用端与管理后台是两套外壳，各管理页都在管理后台下', () => {
    const routes = buildRoutes();
    const top = routes.map((r) => r.path);
    expect(top).toEqual(['/login', '/invite', '/setup', '/', '/admin']);
    const children = (path: string) =>
      routes.find((r) => r.path === path)?.children?.map((r) => r.path ?? 'index') ?? [];
    const app = children('/');
    for (const path of ['chat', 'chat/:conversationId', 'settings']) expect(app).toContain(path);
    expect(app.some((path) => path.startsWith('admin'))).toBe(false);
    const admin = children('/admin');
    for (const path of [
      'index',
      'members',
      'org',
      'grants',
      'models',
      'knowledge',
      'usage',
      'skills',
      'tools',
      'channels',
      'audit',
    ]) {
      expect(admin).toContain(path);
    }
  });
});
