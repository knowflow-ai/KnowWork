import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import GrantsPage from './GrantsPage';
import { validateGrant, subjectOptions } from './form';
import { resourceLabel, subjectLabel, type Directory } from './labels';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';

function api(extra = {}) {
  return routeFetch({
    [`GET ${B}/grants`]: jsonResponse([
      { subject: 'user:u1', resource: 'knowledge_base:s1.*', action: 'use' },
      { subject: 'user:u2', resource: 'knowledge_base:s1.d1', action: 'use' },
      { subject: 'group:g1', resource: 'skill:*', action: 'edit' },
      { subject: 'role:viewer', resource: 'model_group:mg1', action: 'owner' },
    ]),
    [`GET ${B}/members`]: jsonResponse([
      { user_id: 'u1', role: 'editor', name: '李雷', email: 'li@corp.cn' },
      { user_id: 'u2', role: 'viewer', name: '韩梅梅', email: 'han@corp.cn' },
    ]),
    [`GET ${B}/groups`]: jsonResponse([{ id: 'g1', name: '客服', members: [] }]),
    [`GET ${B}/departments`]: jsonResponse([
      { id: 'd1', name: '总部', parent_id: null, members: [], children: [] },
    ]),
    [`GET ${B}/knowledge-bases`]: jsonResponse([
      {
        source_id: 's1',
        source_name: '制度库',
        auth_mode: 'shared',
        enabled: true,
        error: null,
        bases: [{ id: 's1.d1', kb_id: 'd1', name: '员工手册' }],
      },
    ]),
    [`GET ${B}/model-groups`]: jsonResponse([
      { id: 'mg1', name: '通用模型', description: '', model_ids: [] },
    ]),
    [`POST ${B}/grants`]: jsonResponse({}, 201),
    [`DELETE ${B}/grants`]: new Response(null, { status: 204 }),
    ...extra,
  });
}

describe('GrantsPage', () => {
  it('把授权翻成名称显示，可搜索与撤销', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    expect(await screen.findByText('制度库 · 全部知识库')).toBeInTheDocument();
    expect(screen.getByText('制度库 · 员工手册')).toBeInTheDocument();
    expect(await screen.findByText('李雷')).toBeInTheDocument();
    expect(screen.getByText('全部技能')).toBeInTheDocument();
    expect(screen.getByText('只读成员')).toBeInTheDocument();
    expect(await screen.findByText('通用模型')).toBeInTheDocument();
    expect(screen.getByText('完全管理')).toBeInTheDocument();
    const row = screen.getByText('制度库 · 全部知识库').closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '撤销' }));
    const pop = await screen.findByRole('alertdialog', { name: /撤销这条授权/ });
    await userEvent.click(within(pop).getByRole('button', { name: '撤销' }));
    await waitFor(() =>
      expect(calls.find('DELETE', `${B}/grants`)[0]?.query.toString()).toBe(
        'subject=user%3Au1&resource=knowledge_base%3As1.*',
      ),
    );
    fireEvent.change(screen.getByPlaceholderText('搜索对象、资源或权限'), { target: { value: '客服' } });
    expect(screen.queryByText('制度库 · 全部知识库')).not.toBeInTheDocument();
  });

  it('新增授权：知识库按来源分组，可选单个知识库', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    expect(await within(dialog).findByText('请选择授权对象')).toBeInTheDocument();
    await pickOption(dialog, '授权对象', '李雷');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    expect(await within(dialog).findByText('请选择知识库')).toBeInTheDocument();
    await pickOption(dialog, '知识库', '制度库 · 员工手册');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/grants`)[0]?.body).toEqual({
        subject: 'user:u1',
        resource: 'knowledge_base:s1.d1',
        action: 'use',
      }),
    );
  });

  it('新增授权：选「全部知识库」就是这个来源下的全部', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickOption(dialog, '授权对象', '李雷');
    await pickOption(dialog, '知识库', '制度库 · 全部知识库');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/grants`)[0]?.body).toMatchObject({ resource: 'knowledge_base:s1.*' }),
    );
    expect(within(dialog).queryByRole('option', { name: '知识源' })).toBeNull();
  });

  it('授权给全部资源', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickOption(dialog, '对象类型', '用户组');
    await pickOption(dialog, '授权对象', '客服');
    await userEvent.click(within(dialog).getByRole('checkbox', { name: '全部' }));
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/grants`)[0]?.body).toEqual({
        subject: 'group:g1',
        resource: 'knowledge_base:*',
        action: 'use',
      }),
    );
  });
});

describe('授权的纯函数', () => {
  const dir: Directory = {
    members: [],
    groups: [],
    departments: [{ id: 'd1', name: '总部', parentId: null, members: [], children: [] }],
    resources: {},
    knowledge: [],
  };

  it('名称查不到时显示 id', () => {
    expect(subjectLabel('user:x', dir)).toEqual({ kind: '成员', name: 'x' });
    expect(subjectLabel('dept:d1', dir).name).toBe('总部');
    expect(subjectLabel('group:g', dir).name).toBe('g');
    expect(subjectLabel('alien:a', dir)).toEqual({ kind: 'alien', name: 'a' });
    expect(resourceLabel('tool:t1', dir)).toEqual({ type: '工具', name: 't1' });
    expect(resourceLabel('knowledge_source:s9', dir), '旧授权仍能显示').toEqual({
      type: '知识源',
      name: 's9',
    });
    expect(resourceLabel('knowledge_base:*', dir).name).toBe('全部知识库');
  });

  it('校验与选项', () => {
    const base = {
      subjectKind: 'user' as const,
      resourceType: 'tool',
      resourceId: 'bad id',
      all: false,
      action: 'use' as const,
    };
    expect(validateGrant(base)).toBe('请选择授权对象');
    expect(validateGrant({ ...base, subjectId: 'u' })).toMatch(/资源 ID/);
    expect(validateGrant({ ...base, subjectId: 'u', all: true })).toBeNull();
    const kb = { ...base, subjectId: 'u', resourceType: 'knowledge_base' };
    expect(validateGrant({ ...kb, resourceId: '' })).toBe('请选择知识库');
    expect(validateGrant({ ...kb, resourceId: 's1.*' })).toBeNull();
    expect(subjectOptions('dept', dir)).toEqual([{ value: 'd1', label: '总部' }]);
    expect(subjectOptions('role', dir).map((o) => o.value)).toEqual(['owner', 'admin', 'editor', 'viewer']);
  });
});
